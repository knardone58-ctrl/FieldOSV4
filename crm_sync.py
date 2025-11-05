"""
FieldOS V4.2 — CRM Sync Manager
- Single daemon worker thread (guarded for Streamlit reruns)
- Queue → process → synced | cached (offline)
- Snapshot migration to avoid crashes on older files
"""

from __future__ import annotations

import copy
import csv
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, MutableMapping, Optional, Tuple

import requests
import streamlit as st

SNAPSHOT_PATH = Path("data/crm_snapshot.json")
WORKER_NAME = "crm-sync-worker"
OPS_LOG_PATH = Path("data/ops_log.jsonl")
RECENT_PAYLOAD_LIMIT = 5
CRM_SAMPLE_PATH = Path("data/crm_sample.csv")

LOGGER = logging.getLogger(__name__)

DEFAULT_CRM_ENDPOINT = "http://localhost:8787/crm/push"
DEFAULT_CRM_TIMEOUT = 5.0
DEFAULT_CRM_MAX_RETRIES = 3

# Base snapshot guarantees keys for new telemetry fields
BASE_SNAPSHOT = {
    "cached_records": [],
    "last_sync": None,
    "ai_fail_count": 0,
    "ai_latency_totals": {"transcribe": 0.0, "polish": 0.0},
    "ai_latency_counts": {"transcribe": 0, "polish": 0},
    "last_payload": {},
    "recent_payloads": [],
    "last_crm_status": {
        "state": None,
        "timestamp": None,
        "response_code": None,
        "error": None,
    },
}


def _ensure_session_lists(state: Optional[MutableMapping[str, Any]] = None) -> MutableMapping[str, Any]:
    target = st.session_state if state is None else state
    target.setdefault("crm_queue", [])
    target.setdefault("crm_sync_log", [])
    target.setdefault("offline_cache", [])
    target.setdefault("gps", "")
    target.setdefault("ai_fail_count", 0)
    target.setdefault("stream_updates_count", 0)
    target.setdefault("stream_latency_ms_first_partial", None)
    target.setdefault("stream_dropouts", 0)
    target.setdefault(
        "final_worker_stats",
        {
            "queue_depth": 0,
            "last_success_ts": None,
            "last_error": None,
        },
    )
    target.setdefault("crm_snapshot_warning_logged", False)
    target.setdefault("crm_snapshot_warning_pending", False)
    target.setdefault("crm_snapshot_warning_message", "")
    target.setdefault("last_crm_payload", None)
    target.setdefault("last_crm_status", None)
    target.setdefault("crm_delivery_pending", False)
    target.setdefault("crm_delivery_status", None)
    target.setdefault("crm_delivery_message", "")
    target.setdefault("crm_delivery_payload_id", None)
    target.setdefault("crm_retry_available", False)
    target.setdefault("_crm_retry_in_progress", False)
    target.setdefault("_crm_last_delivery_id", None)
    target.setdefault("_draft_note_toasts", [])
    target.setdefault("chat_requests", 0)
    target.setdefault("chat_fallback_count", 0)
    target.setdefault("chat_last_error", None)
    target.setdefault("chat_last_hash", "")
    target.setdefault("chat_last_query", "")
    target.setdefault("chat_positioning_count", 0)
    target.setdefault("crm_processed_count", 0)
    target.setdefault("crm_queue_debug", [])
    return target


def _coerce_json(value: Any) -> Any:
    """Ensure payload fields are JSON serializable."""
    if isinstance(value, dict):
        return {str(key): _coerce_json(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_coerce_json(val) for val in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a deep JSON-serialisable copy of a payload."""
    return _coerce_json(payload)


def _redact_payload_for_log(payload: Dict[str, Any]) -> Dict[str, Any]:
    sensitive_keys = {
        "note",
        "note_polished",
        "transcription_raw",
        "transcription_stream_partial",
        "transcription_final",
    }
    redacted = {}
    for key, value in payload.items():
        if key in sensitive_keys:
            redacted[key] = "[redacted]"
        else:
            redacted[key] = value
    return _normalize_payload(redacted)


def _update_snapshot_with_payload(payload: Dict[str, Any], status: Optional[Dict[str, Any]] = None) -> Tuple[bool, Dict[str, Any]]:
    """Persist last payload (and bounded history) into the snapshot."""
    serialized = _normalize_payload(payload)
    if status is not None:
        serialized["crm_status"] = status
    try:
        current = load_snapshot()
    except Exception as exc:  # pragma: no cover - defensive guard
        LOGGER.warning("Failed to load CRM snapshot before update: %s", exc)
        return False, serialized

    history: Iterable[Dict[str, Any]] = current.get("recent_payloads", [])
    if not isinstance(history, list):
        history = []
    updated_history = [serialized] + list(history)
    updated_history = updated_history[:RECENT_PAYLOAD_LIMIT]

    try:
        save_snapshot(
            {
                "last_payload": serialized,
                "recent_payloads": updated_history,
                "last_crm_status": status or {
                    "state": None,
                    "timestamp": None,
                    "response_code": None,
                    "error": None,
                },
            }
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        LOGGER.warning("Failed to write CRM snapshot: %s", exc)
        return False, serialized
    return True, serialized


def _get_crm_config() -> Dict[str, Any]:
    endpoint = os.getenv("FIELDOS_CRM_ENDPOINT", DEFAULT_CRM_ENDPOINT)
    api_key = os.getenv("FIELDOS_CRM_API_KEY")
    timeout = float(os.getenv("FIELDOS_CRM_TIMEOUT", str(DEFAULT_CRM_TIMEOUT)))
    max_retries = int(os.getenv("FIELDOS_CRM_MAX_RETRIES", str(DEFAULT_CRM_MAX_RETRIES)))
    return {
        "endpoint": endpoint,
        "api_key": api_key,
        "timeout": timeout,
        "max_retries": max(1, max_retries),
    }


def _generate_payload_id(payload: Dict[str, Any]) -> str:
    existing = payload.get("_crm_payload_id") or payload.get("ts")
    if isinstance(existing, str) and existing:
        return existing
    new_id = uuid.uuid4().hex
    payload["_crm_payload_id"] = new_id
    return new_id


def _build_status_meta(state: str, response_code: Optional[int], error: Optional[str]) -> Dict[str, Any]:
    return {
        "state": state,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "response_code": response_code,
        "error": error,
    }


def _handle_snapshot_failure(state: MutableMapping[str, Any], message: str) -> None:
    if not state.get("crm_snapshot_warning_logged", False):
        LOGGER.warning("%s", message)
        state["crm_snapshot_warning_logged"] = True
    state["crm_snapshot_warning_pending"] = True
    state["crm_snapshot_warning_message"] = message


def _clear_snapshot_failure(state: MutableMapping[str, Any]) -> None:
    state["crm_snapshot_warning_logged"] = False
    state["crm_snapshot_warning_pending"] = False
    state["crm_snapshot_warning_message"] = ""


def _append_ops_log(
    status: str,
    *,
    state: Optional[MutableMapping[str, Any]] = None,
    timestamp: Optional[datetime] = None,
    crm_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Append operational telemetry to ops_log.jsonl."""
    OPS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    session = _ensure_session_lists(state)
    ts = timestamp or datetime.now()
    final_stats = session.get("final_worker_stats") or {}
    record = {
        "ts": ts.isoformat(),
        "status": status,
        "queue_len": len(session.get("crm_queue", [])),
        "ai_failures": int(session.get("ai_fail_count", 0) or 0),
        "stream_updates": session.get("stream_updates_count"),
        "stream_latency_ms_first_partial": session.get("stream_latency_ms_first_partial"),
        "stream_dropouts": session.get("stream_dropouts"),
        "final_worker_queue_depth": int(final_stats.get("queue_depth") or 0),
        "final_worker_last_success": final_stats.get("last_success_ts") or final_stats.get("last_success"),
        "final_worker_error": final_stats.get("last_error"),
        "chat_requests": int(session.get("chat_requests", 0) or 0),
        "chat_fallback_count": int(session.get("chat_fallback_count", 0) or 0),
        "chat_last_error": session.get("chat_last_error"),
        "chat_last_hash": session.get("chat_last_hash"),
        "chat_last_query": session.get("chat_last_query"),
        "chat_positioning_count": int(session.get("chat_positioning_count", 0) or 0),
        "crm_processed_count": int(session.get("crm_processed_count", 0) or 0),
    }
    queue_debug = list(session.get("crm_queue_debug", []))
    if queue_debug:
        record["crm_queue_debug"] = queue_debug
        session["crm_queue_debug"] = []
    if crm_meta is not None:
        record.update(
            {
                "crm_response_code": crm_meta.get("crm_response_code"),
                "crm_error": crm_meta.get("crm_error"),
                "crm_attempts": crm_meta.get("crm_attempts"),
            }
        )
    with OPS_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return record


def append_ops_log_event(
    status: str,
    *,
    state: Optional[MutableMapping[str, Any]] = None,
    timestamp: Optional[datetime] = None,
    crm_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Public helper to append operational telemetry (used by QA + tests)."""
    return _append_ops_log(status, state=state, timestamp=timestamp, crm_meta=crm_meta)


def load_snapshot() -> Dict:
    """Load or seed the snapshot; migrate missing keys safely."""
    if not SNAPSHOT_PATH.exists():
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(json.dumps(BASE_SNAPSHOT, indent=2))
    try:
        raw = SNAPSHOT_PATH.read_text()
        snap = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        # File was truncated or corrupted mid-write; fall back to a fresh snapshot.
        snap = {}
    for key, value in BASE_SNAPSHOT.items():
        if key not in snap:
            snap[key] = value if not isinstance(value, dict) else value.copy()
        elif isinstance(value, dict) and isinstance(snap[key], dict):
            for nested_key, nested_value in value.items():
                snap[key].setdefault(nested_key, nested_value)
    SNAPSHOT_PATH.write_text(json.dumps(snap, indent=2))
    return snap


def save_snapshot(update: Dict | None = None) -> None:
    snap = load_snapshot()
    if update:
        for key, value in update.items():
            if isinstance(value, dict) and isinstance(snap.get(key), dict):
                snap[key].update(value)
            else:
                snap[key] = value
    snap["last_sync"] = datetime.now().isoformat()
    SNAPSHOT_PATH.write_text(json.dumps(snap, indent=2))


def enqueue_crm_push(payload: Dict) -> None:
    _ensure_session_lists()
    st.session_state["crm_queue"].append(payload)


def send_to_crm(payload: Dict[str, Any], *, retry_count: int = 0) -> Dict[str, Any]:
    """Send payload to CRM endpoint (or stub)."""
    config = _get_crm_config()
    endpoint = config["endpoint"]
    headers = {"Content-Type": "application/json"}
    if config["api_key"]:
        headers["Authorization"] = f"Bearer {config['api_key']}"

    delivery_payload = copy.deepcopy(payload)
    # Strip internal metadata
    for key in list(delivery_payload.keys()):
        if key.startswith("_crm_") or key == "crm_status":
            delivery_payload.pop(key, None)

    payload_id = _generate_payload_id(payload)
    LOGGER.info(
        "CRM delivery attempt %s for payload %s",
        retry_count + 1,
        payload_id,
    )

    try:
        response = requests.post(
            endpoint,
            json=delivery_payload,
            headers=headers,
            timeout=config["timeout"],
        )
    except requests.RequestException as exc:  # pragma: no cover - network guard
        status_code = getattr(exc.response, "status_code", None)
        LOGGER.warning(
            "CRM delivery network failure for %s: %s",
            payload_id,
            exc,
        )
        return {
            "status": "error",
            "response_code": status_code,
            "error": str(exc),
        }

    if response.status_code >= 200 and response.status_code < 300:
        try:
            body = response.json()
        except ValueError:
            body = None
        return {
            "status": "ok",
            "response_code": response.status_code,
            "body": body,
        }

    error_message = None
    try:
        error_json = response.json()
        error_message = error_json.get("error") if isinstance(error_json, dict) else None
    except ValueError:
        error_message = response.text

    LOGGER.warning(
        "CRM delivery error for %s (code=%s): %s",
        payload_id,
        response.status_code,
        error_message,
    )
    return {
        "status": "error",
        "response_code": response.status_code,
        "error": error_message or f"HTTP {response.status_code}",
    }


CRM_DELIVERY_CLIENT = send_to_crm


def _record_crm_delivery(
    session: MutableMapping[str, Any],
    payload: Dict[str, Any],
    result: Dict[str, Any],
    state_label: str,
    *,
    will_retry: bool = False,
) -> None:
    """Persist delivery outcome, snapshot, telemetry, and UI flags."""
    status_meta = _build_status_meta(
        state_label,
        result.get("response_code"),
        result.get("error"),
    )

    payload_for_snapshot = copy.deepcopy(payload)
    for key in list(payload_for_snapshot.keys()):
        if key.startswith("_crm_") and key not in {"_crm_payload_id"}:
            payload_for_snapshot.pop(key, None)

    snapshot_ok, serialized = _update_snapshot_with_payload(payload_for_snapshot, status_meta)
    if snapshot_ok:
        _clear_snapshot_failure(session)
        session["last_crm_payload"] = serialized
        session["last_crm_status"] = status_meta
    else:
        _handle_snapshot_failure(session, "Unable to persist CRM payload to snapshot.")

    redacted_log = _redact_payload_for_log(payload_for_snapshot)
    payload_id = payload.get("_crm_payload_id")
    session["_crm_last_delivery_id"] = payload_id
    message = ""
    icon_state = state_label

    if state_label == "synced":
        message = f"Synced to CRM at {status_meta['timestamp']}"
        session["crm_retry_available"] = False
        _append_crm_sample_entry(payload_for_snapshot, status_meta)
        session.pop("_crm_sample_rows", None)
    elif state_label == "cached":
        message = "Offline: payload cached for CRM sync"
        session["crm_retry_available"] = True
    elif state_label == "retrying":
        message = "CRM push failed, retrying shortly…"
        session["crm_retry_available"] = False
    else:  # failed
        message = f"CRM push failed: {status_meta['error'] or 'unknown error'}"
        session["crm_retry_available"] = True

    session["crm_delivery_pending"] = True
    session["crm_delivery_status"] = status_meta
    session["crm_delivery_message"] = message
    session["crm_delivery_payload_id"] = payload_id
    if not will_retry:
        session["_crm_retry_in_progress"] = False

    session["crm_processed_count"] = int(session.get("crm_processed_count", 0) or 0) + 1
    session["crm_sync_log"].append({"status": state_label, "payload": copy.deepcopy(payload_for_snapshot)})

    crm_meta = {
        "crm_response_code": status_meta.get("response_code"),
        "crm_error": status_meta.get("error"),
        "crm_attempts": int(payload.get("_crm_retry_attempts", 0)),
    }
    _append_ops_log(state_label, state=session, crm_meta=crm_meta)

    if icon_state == "synced":
        LOGGER.info("CRM delivery succeeded for %s", payload_id)
    elif icon_state == "retrying":
        LOGGER.warning("CRM delivery retry scheduled for %s", payload_id)
    elif icon_state == "cached":
        LOGGER.info("CRM delivery cached offline for %s", payload_id)
    else:
        LOGGER.warning(
            "CRM delivery failed for %s: %s",
            payload_id,
            status_meta.get("error"),
        )
    LOGGER.debug("CRM payload (redacted): %s", redacted_log)


def _cache_payload(
    session: MutableMapping[str, Any],
    payload: Dict[str, Any],
    result: Optional[Dict[str, Any]] = None,
    state_label: str = "cached",
) -> None:
    cached = copy.deepcopy(payload)
    cached["_offline_cached"] = True
    cached["_cached_at"] = datetime.now().isoformat()
    cached["_gps"] = session.get("gps", "")
    session["offline_cache"].append(cached)
    result_meta = result or {"status": "cached", "response_code": None, "error": None}
    _record_crm_delivery(session, cached, result_meta, state_label)


def _remove_offline_cached_entry(session: MutableMapping[str, Any], payload: Dict[str, Any]) -> None:
    """Drop matching payload from offline cache by payload id or timestamp."""
    offline_cache = session.get("offline_cache")
    if not offline_cache:
        return

    identifiers = []
    for key in ("_crm_payload_id", "ts"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            identifiers.append(value)
    if not identifiers:
        return

    session["offline_cache"] = [
        entry
        for entry in offline_cache
        if not any(
            identifier == entry.get("_crm_payload_id") or identifier == entry.get("ts")
            for identifier in identifiers
        )
    ]


def _append_crm_sample_entry(payload: Dict[str, Any], status: Dict[str, Any]) -> None:
    if not CRM_SAMPLE_PATH.exists():
        return
    try:
        with CRM_SAMPLE_PATH.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            existing_rows = list(reader) if fieldnames else []
    except Exception as exc:  # pragma: no cover - defensive guard
        LOGGER.warning("Unable to read CRM sample CSV: %s", exc)
        return

    if not fieldnames:
        return

    row = {name: "" for name in fieldnames}

    customer_id = payload.get("_crm_payload_id") or payload.get("customer_id") or payload.get("ts")
    row["Customer_ID"] = str(customer_id or uuid.uuid4().hex)
    row["Customer_Name"] = str(payload.get("account", ""))
    row["Customer_Type"] = str(payload.get("customer_type", ""))
    row["Primary_Contact"] = str(payload.get("contact_name", ""))
    row["Contact_Phone"] = str(payload.get("contact_phone", ""))
    row["Contact_Email"] = str(payload.get("contact_email", ""))
    row["Lead_Source"] = str(payload.get("lead_source", "Demo"))
    row["Service_Interest"] = str(payload.get("service", ""))

    address = str(payload.get("account_address", ""))
    if address:
        parts = [part.strip() for part in address.split(",")]
        if parts:
            row["Property_Address"] = parts[0]
        if len(parts) >= 2:
            row["City"] = parts[1]
        if len(parts) >= 3:
            state_parts = parts[2].split()
            if state_parts:
                row["State"] = state_parts[0]
            if len(state_parts) > 1:
                row["Zip_Code"] = state_parts[1]

    quote = payload.get("quote_summary") or {}
    total = quote.get("total")
    if isinstance(total, (int, float)):
        row["Estimated_Contract_Value_Num"] = f"{total:.2f}"
        row["Estimated_Contract_Value"] = f"${total:,.0f}"

    row["Stage"] = str(status.get("state", "")).capitalize()
    timestamp = status.get("timestamp") or ""
    if isinstance(timestamp, str) and "T" in timestamp:
        row["Last_Contact_Date"] = timestamp.split("T")[0]
    row["Assigned_Rep"] = str(payload.get("assigned_rep", ""))
    row["Region"] = str(payload.get("region", ""))
    note_text = str(payload.get("note", "")).replace("\n", " ").strip()
    row["Notes"] = note_text
    row["Summary"] = note_text[:120]
    row["Needs_Follow_Up"] = "False"

    existing_index = None
    for idx, existing in enumerate(existing_rows):
        if existing.get("Customer_ID") == row["Customer_ID"]:
            existing_index = idx
            break
    if existing_index is not None:
        existing_rows[existing_index] = row
    else:
        existing_rows.append(row)

    try:
        with CRM_SAMPLE_PATH.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(existing_rows)
    except Exception as exc:  # pragma: no cover - defensive guard
        LOGGER.warning("Unable to update CRM sample CSV: %s", exc)


def _safe_send_to_crm(payload: Dict[str, Any], retry_count: int) -> Dict[str, Any]:
    try:
        return CRM_DELIVERY_CLIENT(payload, retry_count=retry_count)
    except Exception as exc:  # pragma: no cover - defensive guard
        LOGGER.warning("CRM client raised unexpected error: %s", exc)
        return {"status": "error", "response_code": None, "error": str(exc)}


def _process_payload(payload: Dict, offline: bool) -> None:
    session = _ensure_session_lists()
    payload_copy = copy.deepcopy(payload)
    payload_id = _generate_payload_id(payload_copy)

    max_retries = _get_crm_config()["max_retries"]
    retry_attempts = int(payload_copy.get("_crm_retry_attempts", 0))

    if offline or session.get("offline", False):
        payload_copy["_crm_retry_attempts"] = retry_attempts
        _cache_payload(session, payload_copy, state_label="cached")
        return

    result = _safe_send_to_crm(payload_copy, retry_attempts)
    if result.get("status") == "ok":
        payload_copy.pop("_crm_retry_attempts", None)
        payload_copy.pop("_crm_last_error", None)
        _record_crm_delivery(session, payload_copy, result, "synced")
        _remove_offline_cached_entry(session, payload_copy)
        return

    # Failure path
    retry_attempts += 1
    payload_copy["_crm_retry_attempts"] = retry_attempts
    payload_copy["_crm_last_error"] = result.get("error")

    if retry_attempts < max_retries and not session.get("offline", False):
        session["crm_queue"].append(copy.deepcopy(payload_copy))
        session["_crm_last_delivery_id"] = payload_id
        _record_crm_delivery(session, payload_copy, result, "retrying", will_retry=True)
        return

    _cache_payload(session, payload_copy, result=result, state_label="failed")


def _worker_loop() -> None:
    while True:
        _ensure_session_lists()
        if st.session_state["crm_queue"]:
            payload = st.session_state["crm_queue"].pop(0)
            time.sleep(0.35)
            _process_payload(payload, st.session_state.get("offline", False))
        else:
            time.sleep(0.2)


def start_crm_worker() -> None:
    names = [thread.name for thread in threading.enumerate()]
    if WORKER_NAME in names:
        return
    worker = threading.Thread(target=_worker_loop, name=WORKER_NAME, daemon=True)
    try:
        from streamlit.runtime.scriptrunner import add_script_run_ctx  # type: ignore

        add_script_run_ctx(worker)
    except Exception:
        pass
    worker.start()


def flush_offline_cache() -> int:
    session = _ensure_session_lists()
    if session.get("offline", False):
        return 0
    flushed = 0
    remaining: List[Dict] = []
    for record in session.get("offline_cache", []):
        payload_copy = copy.deepcopy(record)
        max_retries = _get_crm_config()["max_retries"]
        retry_attempts = int(payload_copy.get("_crm_retry_attempts", 0))
        result = _safe_send_to_crm(payload_copy, retry_attempts)
        if result.get("status") == "ok":
            payload_copy.pop("_crm_retry_attempts", None)
            payload_copy.pop("_crm_last_error", None)
            payload_copy.pop("_offline_cached", None)
            payload_copy.pop("_cached_at", None)
            payload_copy.pop("_gps", None)
            flushed += 1
            _record_crm_delivery(session, payload_copy, result, "synced")
        else:
            retry_attempts += 1
            payload_copy["_crm_retry_attempts"] = retry_attempts
            payload_copy["_crm_last_error"] = result.get("error")
            if retry_attempts >= max_retries:
                _record_crm_delivery(session, payload_copy, result, "failed")
            else:
                remaining.append(payload_copy)
                _record_crm_delivery(session, payload_copy, result, "failed")
    session["offline_cache"] = remaining
    if flushed:
        _append_ops_log("flushed", state=session)
    return flushed

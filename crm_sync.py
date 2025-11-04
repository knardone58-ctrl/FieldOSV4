"""
FieldOS V4.2 — CRM Sync Manager
- Single daemon worker thread (guarded for Streamlit reruns)
- Queue → process → synced | cached (offline)
- Snapshot migration to avoid crashes on older files
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, MutableMapping, Optional

import streamlit as st

SNAPSHOT_PATH = Path("data/crm_snapshot.json")
WORKER_NAME = "crm-sync-worker"
OPS_LOG_PATH = Path("data/ops_log.jsonl")

# Base snapshot guarantees keys for new telemetry fields
BASE_SNAPSHOT = {
    "cached_records": [],
    "last_sync": None,
    "ai_fail_count": 0,
    "ai_latency_totals": {"transcribe": 0.0, "polish": 0.0},
    "ai_latency_counts": {"transcribe": 0, "polish": 0},
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
    return target


def _append_ops_log(
    status: str,
    *,
    state: Optional[MutableMapping[str, Any]] = None,
    timestamp: Optional[datetime] = None,
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
    }
    with OPS_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return record


def append_ops_log_event(
    status: str,
    *,
    state: Optional[MutableMapping[str, Any]] = None,
    timestamp: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Public helper to append operational telemetry (used by QA + tests)."""
    return _append_ops_log(status, state=state, timestamp=timestamp)


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


def _process_payload(payload: Dict, offline: bool) -> None:
    if offline:
        cached = {
            **payload,
            "_offline_cached": True,
            "_cached_at": datetime.now().isoformat(),
            "_gps": st.session_state.get("gps", ""),
        }
        st.session_state["offline_cache"].append(cached)
        st.session_state["crm_sync_log"].append({"status": "cached", "payload": cached})
        _append_ops_log("cached")
        save_snapshot()
    else:
        st.session_state["crm_sync_log"].append({"status": "synced", "payload": payload})
        _append_ops_log("synced")
        save_snapshot()


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
    if st.session_state.get("offline", False):
        return 0
    flushed = 0
    remaining: List[Dict] = []
    for record in st.session_state.get("offline_cache", []):
        try:
            st.session_state["crm_sync_log"].append({"status": "synced", "payload": record})
            flushed += 1
        except Exception:
            remaining.append(record)
    st.session_state["offline_cache"] = remaining
    save_snapshot()
    if flushed:
        _append_ops_log("flushed")
    return flushed

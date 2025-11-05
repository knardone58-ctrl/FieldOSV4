from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, MutableMapping


def _safe_ids(payloads: Iterable[Dict[str, Any]]) -> list[str]:
    identifiers: list[str] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in ("_crm_payload_id", "ts"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                identifiers.append(value)
                break
    return identifiers


def _lookup(state: MutableMapping[str, Any], key: str, default: Any) -> Any:
    try:
        return state[key]
    except KeyError:
        return default


def capture_crm_state(label: str, session_state: MutableMapping[str, Any]) -> Dict[str, Any]:
    queue: list[Dict[str, Any]] = list(_lookup(session_state, "crm_queue", []))
    offline_cache: list[Dict[str, Any]] = list(_lookup(session_state, "offline_cache", []))
    retrying = bool(_lookup(session_state, "_crm_retry_in_progress", False))
    retry_available = bool(_lookup(session_state, "crm_retry_available", False))
    processed = int(_lookup(session_state, "crm_processed_count", 0) or 0)
    last_status = _lookup(session_state, "last_crm_status", None)
    snapshot = {
        "event": label,
        "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
        "queue_len": len(queue),
        "offline_cache": len(offline_cache),
        "retry_in_progress": retrying,
        "retry_available": retry_available,
        "processed_count": processed,
        "queue_ids": _safe_ids(queue),
        "offline_cache_ids": _safe_ids(offline_cache),
        "last_status": last_status,
    }
    debug_log = list(_lookup(session_state, "crm_queue_debug", []))
    debug_log.append(snapshot)
    session_state["crm_queue_debug"] = debug_log
    return snapshot

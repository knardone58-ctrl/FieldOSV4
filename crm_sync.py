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
from typing import Dict, List

import streamlit as st

SNAPSHOT_PATH = Path("data/crm_snapshot.json")
WORKER_NAME = "crm-sync-worker"

# Base snapshot guarantees keys for new telemetry fields
BASE_SNAPSHOT = {
    "cached_records": [],
    "last_sync": None,
    "ai_fail_count": 0,
    "ai_latency_totals": {"transcribe": 0.0, "polish": 0.0},
    "ai_latency_counts": {"transcribe": 0, "polish": 0},
}


def _ensure_session_lists() -> None:
    st.session_state.setdefault("crm_queue", [])
    st.session_state.setdefault("crm_sync_log", [])
    st.session_state.setdefault("offline_cache", [])
    st.session_state.setdefault("gps", "")


def load_snapshot() -> Dict:
    """Load or seed the snapshot; migrate missing keys safely."""
    if not SNAPSHOT_PATH.exists():
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(json.dumps(BASE_SNAPSHOT, indent=2))
    snap = json.loads(SNAPSHOT_PATH.read_text())
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
        save_snapshot()
    else:
        st.session_state["crm_sync_log"].append({"status": "synced", "payload": payload})
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
    return flushed

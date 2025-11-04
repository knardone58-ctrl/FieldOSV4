"""FieldOS Ops Dashboard (prototype).

Launch with:
    streamlit run ops_dashboard.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

import streamlit as st

OPS_LOG_PATH = Path("data/ops_log.jsonl")
QUEUE_WARN_THRESHOLD = 3


@st.cache_data(show_spinner=False)
def load_ops_entries() -> list[dict]:
    if not OPS_LOG_PATH.exists():
        return []
    entries: list[dict] = []
    for line in OPS_LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _avg(values: list[float]) -> str:
    return f"{mean(values):.1f}" if values else "—"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def main() -> None:
    st.set_page_config(page_title="FieldOS Ops Dashboard", layout="wide")
    st.title("FieldOS Ops Dashboard")

    entries = load_ops_entries()
    if not entries:
        st.warning("No ops log entries found at data/ops_log.jsonl")
        return

    latency_values = [
        e["stream_latency_ms_first_partial"] for e in entries if e.get("stream_latency_ms_first_partial") is not None
    ]
    updates_values = [e.get("stream_updates") for e in entries if isinstance(e.get("stream_updates"), (int, float))]
    total_dropouts = sum(int(e.get("stream_dropouts") or 0) for e in entries)
    latest = entries[-1]
    latest_queue = int(latest.get("final_worker_queue_depth") or 0)
    latest_error = latest.get("final_worker_error")
    latest_success = latest.get("final_worker_last_success")
    success_dt = _parse_ts(latest_success)
    success_display = success_dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z") if success_dt else "—"

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Entries", len(entries))
    col2.metric("Avg First Partial (ms)", _avg(latency_values))
    col3.metric("Avg Updates", _avg(updates_values))
    col4.metric("Total Dropouts", total_dropouts)
    col5.metric("AI Failures (latest)", latest.get("ai_failures", 0))
    delta = "⚠️ backlog" if latest_queue > QUEUE_WARN_THRESHOLD else None
    col6.metric("Final Worker Queue", latest_queue, delta=delta)

    st.markdown(f"**Last Event:** {latest.get('ts', '—')} ({latest.get('status', 'unknown')})")
    st.markdown(f"**Final Worker Last Success:** {success_display}")
    if latest_error:
        st.error(f"Final Worker Error: {latest_error}")

    st.subheader("Ops Log Entries")
    st.dataframe(entries)


if __name__ == "__main__":
    main()

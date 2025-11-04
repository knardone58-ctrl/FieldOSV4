"""FieldOS Ops Dashboard (prototype).

Launch with:
    streamlit run ops_dashboard.py
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

import streamlit as st

OPS_LOG_PATH = Path("data/ops_log.jsonl")


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

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Entries", len(entries))
    col2.metric("Avg First Partial (ms)", _avg(latency_values))
    col3.metric("Avg Updates", _avg(updates_values))
    col4.metric("Total Dropouts", total_dropouts)
    col5.metric("AI Failures (latest)", latest.get("ai_failures", 0))

    st.markdown(f"**Last Event:** {latest.get('ts', '—')} ({latest.get('status', 'unknown')})")

    st.subheader("Ops Log Entries")
    st.dataframe(entries)


if __name__ == "__main__":
    main()

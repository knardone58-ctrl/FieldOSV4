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

    latest = entries[-1]
    latency_values = [
        e["stream_latency_ms_first_partial"] for e in entries if e.get("stream_latency_ms_first_partial") is not None
    ]
    updates_values = [e.get("stream_updates") for e in entries if isinstance(e.get("stream_updates"), (int, float))]
    total_dropouts = sum(int(e.get("stream_dropouts") or 0) for e in entries)
    total_success = sum(1 for e in entries if not e.get("final_worker_error"))
    success_rate = (total_success / len(entries)) * 100 if entries else 0.0
    crm_entries = [e for e in entries if e.get("crm_response_code") is not None]
    crm_success_count = sum(1 for e in crm_entries if not e.get("crm_error") and (e.get("status") == "synced"))
    crm_success_rate = (crm_success_count / len(crm_entries)) * 100 if crm_entries else 0.0
    latest_crm_error_entry = next((e for e in reversed(entries) if e.get("crm_error")), None)
    latest_crm_error = latest_crm_error_entry.get("crm_error") if latest_crm_error_entry else None
    latest_crm_code = latest.get("crm_response_code")
    latest_queue = int(latest.get("final_worker_queue_depth") or 0)
    latest_error = latest.get("final_worker_error")
    latest_success = latest.get("final_worker_last_success")
    success_dt = _parse_ts(latest_success)
    success_display = success_dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z") if success_dt else "—"
    queue_depths = [int(e.get("final_worker_queue_depth") or 0) for e in entries]
    queue_recent = queue_depths[-5:]
    queue_trend_delta = queue_recent[-1] - queue_recent[0] if len(queue_recent) >= 2 else 0

    latest_chat_requests = int(latest.get("chat_requests") or 0)
    latest_chat_fallbacks = int(latest.get("chat_fallback_count") or 0)
    chat_fallback_pct = (latest_chat_fallbacks / latest_chat_requests * 100) if latest_chat_requests else 0.0
    latest_chat_error = latest.get("chat_last_error")
    latest_chat_identifier = latest.get("chat_last_hash") or latest.get("chat_last_query") or "—"
    latest_chat_positioning = int(latest.get("chat_positioning_count") or 0)

    col1, col2, col3, col4, col5, col6, col7 = st.columns(7)
    col1.metric("Entries", len(entries))
    col2.metric("Avg First Partial (ms)", _avg(latency_values))
    col3.metric("Avg Updates", _avg(updates_values))
    col4.metric("Total Dropouts", total_dropouts)
    col5.metric("AI Failures (latest)", latest.get("ai_failures", 0))
    delta = "⚠️ backlog" if latest_queue > QUEUE_WARN_THRESHOLD else None
    col6.metric("Final Worker Queue", latest_queue, delta=delta)
    success_delta = "⚠️ low" if success_rate < 80 else None
    col7.metric("Final Worker Success", f"{success_rate:.1f}%", delta=success_delta)

    crm_col1, crm_col2, crm_col3 = st.columns(3)
    crm_col1.metric("CRM Success Rate", f"{crm_success_rate:.1f}%")
    crm_col2.metric("Latest CRM Code", latest_crm_code or "—")
    crm_col3.metric("CRM Errors Logged", sum(1 for e in entries if e.get("crm_error")))

    chat_col1, chat_col2, chat_col3, chat_col4 = st.columns(4)
    chat_col1.metric("Copilot Requests", latest_chat_requests)
    chat_col2.metric("Copilot Fallback Rate", f"{chat_fallback_pct:.1f}%")
    chat_col3.metric("Positioning Briefs", latest_chat_positioning)
    chat_col4.metric("Copilot Last Query/Hash", latest_chat_identifier)

    st.markdown(f"**Last Event:** {latest.get('ts', '—')} ({latest.get('status', 'unknown')})")
    st.markdown(f"**Final Worker Last Success:** {success_display}")
    if latest_error:
        st.error(f"Final Worker Error: {latest_error}")
    if latest_crm_error:
        st.warning(f"Latest CRM error: {latest_crm_error}")
    if latest_chat_error:
        st.warning(f"Latest Copilot error: {latest_chat_error}")
    if queue_recent:
        st.caption(f"Queue trend (last {len(queue_recent)} entries): {queue_trend_delta:+d}")

    st.subheader("Final Worker Queue Depth Trend")
    st.line_chart(queue_depths)

    st.subheader("Ops Log Entries")
    st.dataframe(entries)


if __name__ == "__main__":
    main()

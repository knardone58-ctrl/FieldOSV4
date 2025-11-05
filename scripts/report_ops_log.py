#!/usr/bin/env python3
"""Generate a Markdown summary from data/ops_log.jsonl."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import List, Optional

OPS_LOG_PATH = Path("data/ops_log.jsonl")
QUEUE_WARN_THRESHOLD = 3


def _load_entries() -> List[dict]:
    if not OPS_LOG_PATH.exists():
        return []
    entries = []
    for line in OPS_LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
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


def _fmt_ts(value: Optional[str]) -> str:
    dt = _parse_ts(value)
    if not dt:
        return value or "—"
    local_dt = dt.astimezone()
    return local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_markdown(entries: List[dict]) -> str:
    if not entries:
        return "No ops log entries found."

    latency_values = [e["stream_latency_ms_first_partial"] for e in entries if e.get("stream_latency_ms_first_partial") is not None]
    updates_values = [e.get("stream_updates") for e in entries if isinstance(e.get("stream_updates"), (int, float))]
    total_dropouts = sum(int(e.get("stream_dropouts") or 0) for e in entries)
    total_success = sum(1 for e in entries if not e.get("final_worker_error"))
    success_rate = (total_success / len(entries)) * 100 if entries else 0.0
    crm_entries = [e for e in entries if e.get("crm_response_code") is not None]
    crm_success_count = sum(1 for e in crm_entries if not e.get("crm_error") and (e.get("status") == "synced"))
    crm_success_rate = (crm_success_count / len(crm_entries)) * 100 if crm_entries else 0.0
    latest_crm_error_entry = next((e for e in reversed(entries) if e.get("crm_error")), None)
    latest_crm_error = latest_crm_error_entry.get("crm_error") if latest_crm_error_entry else None
    queue_recent = [int(e.get("final_worker_queue_depth") or 0) for e in entries[-5:]]
    queue_trend_delta = queue_recent[-1] - queue_recent[0] if len(queue_recent) >= 2 else 0
    latest = entries[-1]
    latest_queue = int(latest.get("final_worker_queue_depth") or 0)
    latest_error = latest.get("final_worker_error")
    latest_success = latest.get("final_worker_last_success")
    latest_crm_code = latest.get("crm_response_code")
    latest_chat_requests = int(latest.get("chat_requests") or 0)
    latest_chat_fallbacks = int(latest.get("chat_fallback_count") or 0)
    chat_fallback_pct = (latest_chat_fallbacks / latest_chat_requests * 100) if latest_chat_requests else 0.0
    latest_chat_error = latest.get("chat_last_error")
    latest_chat_identifier = latest.get("chat_last_hash") or latest.get("chat_last_query") or "—"
    latest_chat_positioning = int(latest.get("chat_positioning_count") or 0)

    def _avg(values: List[Optional[float]]) -> str:
        return f"{mean(values):.1f}" if values else "—"

    warnings: List[str] = []
    if latest_queue > QUEUE_WARN_THRESHOLD:
        warnings.append(
            f"Final worker queue depth {latest_queue} exceeds threshold ({QUEUE_WARN_THRESHOLD}). Consider scaling or disabling the worker."
        )
    if latest_error:
        warnings.append(f"Final worker reported error: {latest_error}")
    if success_rate < 80:
        warnings.append(f"Final worker success rate below target: {success_rate:.1f}% (goal ≥ 80%).")
    if crm_entries and crm_success_rate < 90:
        warnings.append(f"CRM success rate below target: {crm_success_rate:.1f}% (goal ≥ 90%).")
    if latest_crm_error:
        warnings.append(f"Latest CRM error: {latest_crm_error}")
    if latest_chat_error:
        warnings.append(f"Latest copilot error: {latest_chat_error}")

    lines = [
        "## Ops Log Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Entries | {len(entries)} |",
        f"| Avg first partial (ms) | {_avg(latency_values)} |",
        f"| Avg streaming updates | {_avg(updates_values)} |",
        f"| Total dropouts | {total_dropouts} |",
        f"| AI failures (latest) | {latest.get('ai_failures', 0)} |",
        f"| Last event | {latest.get('ts', '—')} ({latest.get('status', 'unknown')}) |",
        f"| Final worker success rate | {success_rate:.1f}% |",
        f"| Final worker queue (latest) | {latest_queue} |",
        f"| Final worker queue trend (last {len(queue_recent)} entries) | {queue_trend_delta:+d} |",
        f"| Final worker last success | {_fmt_ts(latest_success)} |",
        f"| Final worker error (latest) | {latest_error or '—'} |",
        f"| CRM success rate | {crm_success_rate:.1f}% |",
        f"| Latest CRM response code | {latest_crm_code or '—'} |",
        f"| Latest CRM error | {latest_crm_error or '—'} |",
        f"| Copilot requests (cumulative) | {latest_chat_requests} |",
        f"| Copilot fallback rate | {chat_fallback_pct:.1f}% |",
        f"| Copilot positioning briefs | {latest_chat_positioning} |",
        f"| Copilot last query/hash | {latest_chat_identifier} |",
        f"| Copilot last error | {latest_chat_error or '—'} |",
    ]
    if warnings:
        lines.extend(["", "### Warnings"])
        for warning in warnings:
            lines.append(f"- {warning}")
    return "\n".join(lines)


def main() -> None:
    summary = _format_markdown(_load_entries())
    print(summary)


if __name__ == "__main__":
    main()

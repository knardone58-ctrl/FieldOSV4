#!/usr/bin/env python3
"""Generate a Markdown summary from data/ops_log.jsonl."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import List, Optional

OPS_LOG_PATH = Path("data/ops_log.jsonl")


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


def _format_markdown(entries: List[dict]) -> str:
    if not entries:
        return "No ops log entries found."

    latency_values = [e["stream_latency_ms_first_partial"] for e in entries if e.get("stream_latency_ms_first_partial") is not None]
    updates_values = [e.get("stream_updates") for e in entries if isinstance(e.get("stream_updates"), (int, float))]
    total_dropouts = sum(int(e.get("stream_dropouts") or 0) for e in entries)
    latest = entries[-1]

    def _avg(values: List[Optional[float]]) -> str:
        return f"{mean(values):.1f}" if values else "—"

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
    ]
    return "\n".join(lines)


def main() -> None:
    summary = _format_markdown(_load_entries())
    print(summary)


if __name__ == "__main__":
    main()

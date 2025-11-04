#!/usr/bin/env python3
"""Regenerate docs/fieldos_narrative/product_timeline.md from timeline.json."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

ROOT = Path(__file__).resolve().parents[1]
TIMELINE_JSON = ROOT / "docs" / "fieldos_narrative" / "timeline.json"
OUTPUT_MD = ROOT / "docs" / "fieldos_narrative" / "product_timeline.md"


def load_entries() -> List[Dict[str, Any]]:
    if not TIMELINE_JSON.exists():
        raise SystemExit(f"Timeline data missing at {TIMELINE_JSON}")
    with TIMELINE_JSON.open(encoding="utf-8") as fh:
        entries: List[Dict[str, Any]] = json.load(fh)
    for entry in entries:
        if "date" not in entry or "title" not in entry:
            raise ValueError(f"Invalid entry (missing date/title): {entry}")
        # Normalize date to ISO format
        entry["_date_obj"] = datetime.strptime(entry["date"], "%Y-%m-%d")
    entries.sort(key=lambda e: e["_date_obj"])
    return entries


def render(entries: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("# FieldOS Product Journey Timeline")
    lines.append("")
    lines.append("> Generated via `python scripts/update_product_timeline.py`. Edit `docs/fieldos_narrative/timeline.json` and rerun to update.")
    lines.append("")
    lines.append("| Date | Release | Headline |")
    lines.append("| --- | --- | --- |")
    for entry in entries:
        date_str = entry["_date_obj"].strftime("%Y-%m-%d")
        tag = entry.get("tag", "—")
        lines.append(f"| {date_str} | {tag} | {entry['title']} |")
    lines.append("")
    for entry in entries:
        date_str = entry["_date_obj"].strftime("%Y-%m-%d")
        tag = entry.get("tag", "—")
        header = f"## {date_str} · FieldOS {tag} — {entry['title']}"
        lines.append(header.strip())
        lines.append("")
        summary = entry.get("summary")
        if summary:
            lines.append(f"**Summary:** {summary}")
            lines.append("")
        highlights = entry.get("highlights") or []
        if highlights:
            lines.append("**Highlights**")
            for bullet in highlights:
                lines.append(f"- {bullet}")
            lines.append("")
        artifacts = entry.get("artifacts") or []
        if artifacts:
            lines.append("**Key Artifacts**")
            for artifact in artifacts:
                lines.append(f"- `{artifact}`")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    entries = load_entries()
    markdown = render(entries)
    OUTPUT_MD.write_text(markdown, encoding="utf-8")
    print(f"Wrote {OUTPUT_MD}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Utility to scrub CRM snapshot payloads before commits or sharing artifacts.

Usage:
    python3 scripts/cleanup_snapshot.py

The script is idempotent and safe for CI: it leaves telemetry counters intact
while clearing last_payload and recent_payloads.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

SNAPSHOT_PATH = Path("data/crm_snapshot.json")


def main() -> int:
    if not SNAPSHOT_PATH.exists():
        print("crm_snapshot.json not found; nothing to scrub.")
        return 0
    try:
        raw = SNAPSHOT_PATH.read_text(encoding="utf-8")
        snapshot: Dict[str, Any] = json.loads(raw) if raw.strip() else {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Failed to load snapshot: {exc}", file=sys.stderr)
        return 1

    modified = False
    if snapshot.get("last_payload"):
        snapshot["last_payload"] = {}
        modified = True
    if snapshot.get("recent_payloads"):
        snapshot["recent_payloads"] = []
        modified = True
    if snapshot.get("last_crm_status"):
        snapshot["last_crm_status"] = {
            "state": None,
            "timestamp": None,
            "response_code": None,
            "error": None,
        }
        modified = True

    if not modified:
        print("Snapshot already scrubbed.")
        return 0

    try:
        SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    except OSError as exc:  # pragma: no cover - defensive guard
        print(f"Failed to write snapshot: {exc}", file=sys.stderr)
        return 1

    print("Scrubbed CRM snapshot payloads and statuses.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

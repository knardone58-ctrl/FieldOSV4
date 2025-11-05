#!/usr/bin/env python3
"""Remove transient demo rows from data/crm_sample.csv.

Keeps baseline CRM sample data (Customer_IDs beginning with "C") and
optionally preserved demo rows (Customer_IDs beginning with "DEMO-").
Rows whose Customer_ID is a timestamp (e.g. "2025-11-04T...") or a
UUID-like value are considered transient and will be removed.

Usage:
    python3 scripts/reset_crm_sample.py [--keep-demo]

The script rewrites data/crm_sample.csv in place and prints a short summary
of how many rows were retained/removed.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

CSV_PATH = Path("data/crm_sample.csv")


def should_keep(customer_id: str, keep_demo: bool) -> bool:
    if not customer_id:
        return False
    if customer_id.startswith("C"):
        return True
    if keep_demo and customer_id.startswith("DEMO-"):
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean CRM sample CSV")
    parser.add_argument(
        "--keep-demo",
        action="store_true",
        help="Preserve demo rows (Customer_ID starting with 'DEMO-').",
    )
    args = parser.parse_args()

    if not CSV_PATH.exists():
        print(f"CRM sample not found: {CSV_PATH}")
        return

    with CSV_PATH.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            print("CRM sample is empty; nothing to clean.")
            return
        rows = list(reader)

    kept = []
    dropped = []
    for row in rows:
        if not row or all(not (cell or "").strip() for cell in row):
            dropped.append(row)
            continue

        customer_id = row[0] if row else ""
        if should_keep(customer_id, args.keep_demo):
            kept.append(row)
        else:
            dropped.append(row)

    with CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(kept)

    print(f"Cleaned CRM sample: kept {len(kept)} rows, removed {len(dropped)} rows.")


if __name__ == "__main__":
    main()

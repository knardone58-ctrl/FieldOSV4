#!/usr/bin/env bash
# Stage UX-related demo files, run mock smoke, and commit.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

FILES=(
  README.md
  app.py
  docs/final_worker_runbook.md
  docs/faster_whisper_checklist.md
  docs/fieldos_narrative/product_timeline.md
  docs/fieldos_narrative/timeline.json
)

echo "Tracking demo files..."
git add "${FILES[@]}"

echo "Running mock smoke..."
FIELDOS_FINAL_WORKER_ENABLED=true FIELDOS_FINAL_WORKER_MOCK=true scripts/run_final_worker_smoke.sh

READOUT=(README.md docs/final_worker_runbook.md)
echo "Compile sanity..."
python3 -m compileall "${READOUT[@]}" app.py >/dev/null

git status --short
echo "Run 'git commit -m "Polish UX demo flow"' when ready."

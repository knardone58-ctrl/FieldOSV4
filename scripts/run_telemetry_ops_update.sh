#!/usr/bin/env bash
# FieldOS V4.4 Telemetry & Ops Reporting rollout script.
# Executes validation, staging, and commit/push steps described in the execution brief.

set -euo pipefail

REPO_ROOT="/Users/kevinnardone/FieldOSV4"
COMMIT_MESSAGE="Surface streaming metrics and seed ops reporting"

echo "📍 Moving to ${REPO_ROOT}"
cd "${REPO_ROOT}"

echo "✅ Step 1: static import check"
python3 -m compileall app.py crm_sync.py audio_cache.py tests/test_audio_cache.py tests/test_ops_log.py

echo "✅ Step 2: unit tests"
./venv/bin/python -m pytest tests/test_audio_cache.py tests/test_ops_log.py

echo "✅ Step 3: deterministic streaming QA (executed from repo parent)"
(cd "${REPO_ROOT}/.." && FIELDOS_QA_MODE=true FieldOSV4/venv/bin/python FieldOSV4/qa/test_fieldos_streaming_deterministic.py)

echo "✅ Step 4: ops log report sanity (empty log)"
rm -f data/ops_log.jsonl
python3 scripts/report_ops_log.py || true

echo "✅ Step 5: stage tracked files"
git add README.md app.py crm_sync.py audio_cache.py tests/ qa/qa_suite.sh scripts/report_ops_log.py ops_dashboard.py .github/workflows/qa-suite.yml

echo "🚧 Step 6: commit"
git commit -m "${COMMIT_MESSAGE}"

echo "🚀 Step 7: push to origin/main"
git push origin main

echo "🔍 Step 8: final status"
git status

echo "🎉 Telemetry & ops reporting update complete."

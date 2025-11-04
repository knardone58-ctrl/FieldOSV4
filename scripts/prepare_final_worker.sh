#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

git add \
  app.py \
  final_transcriber.py \
  fieldos_config.py \
  tests/test_final_worker.py \
  qa/test_fieldos_regression.py \
  qa/qa_suite.sh \
  docs/final_worker_prototype.md \
  docs/faster_whisper_checklist.md \
  scripts/run_streaming_session.sh \
  scripts/run_final_worker_smoke.sh \
  scripts/build_faster_whisper_from_source.sh \
  scripts/start_final_worker.py \
  README.md \
  pytest.ini

FIELDOS_FINAL_WORKER_MOCK=true ./venv/bin/python -m pytest tests/test_final_worker.py -q

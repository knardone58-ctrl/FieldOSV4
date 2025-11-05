#!/usr/bin/env bash
# Smoke-test the final transcription worker against a local audio clip.
#
# Usage:
#   scripts/run_final_worker_smoke.sh [--clip PATH] [additional start_final_worker.py args...]
# Examples:
#   scripts/run_final_worker_smoke.sh
#   scripts/run_final_worker_smoke.sh --clip data/audio_cache/clip_123.wav --stay-alive

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ ! -d "venv" ]]; then
  echo "❌ Missing virtualenv at ${ROOT_DIR}/venv. Run scripts/setup_env.sh first." >&2
  exit 1
fi

CLIP="data/audio_cache/sample.wav"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --clip)
      if [[ $# -lt 2 ]]; then
        echo "Missing argument for --clip" >&2
        exit 1
      fi
      CLIP="$2"
      shift 2
      ;;
    -*)
      EXTRA_ARGS+=("$1")
      shift
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

# Resolve clip path; if placeholder or missing, pick first readable WAV.
resolve_clip() {
  python3 - <<'PY'
import os
from pathlib import Path
candidate = Path(os.environ["CLIP_ARG"]).expanduser()
print(candidate)
PY
}

CLIP_PATH="$(CLIP_ARG="${CLIP}" resolve_clip)"

if [[ ! -f "${CLIP_PATH}" || "${CLIP_PATH}" == "path/to/your.wav" ]]; then
  mapfile -t CANDIDATES < <(find data/audio_cache -maxdepth 1 -type f -name '*.wav' -readable -size +0c 2>/dev/null | sort)
  if [[ ${#CANDIDATES[@]} -gt 0 ]]; then
    CLIP_PATH="${CANDIDATES[0]}"
    echo "ℹ️  Using clip ${CLIP_PATH}"
  else
    echo "❌ No readable WAV files found under data/audio_cache/. Provide a clip via --clip." >&2
    exit 1
  fi
fi

if [[ ! -f "${CLIP_PATH}" ]]; then
  echo "❌ Clip not found: ${CLIP_PATH}. Provide an existing file via --clip." >&2
  exit 1
fi

PYTHON_BIN="./venv/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "❌ Unable to execute ${PYTHON_BIN}. Verify scripts/setup_env.sh completed successfully." >&2
  exit 1
fi

MODEL="${FIELDOS_WHISPER_MODEL:-base}"
if [[ ! -d "data/models/faster-whisper/${MODEL}" ]]; then
  if [[ "${FIELDOS_DOWNLOAD_FASTER_WHISPER:-auto}" == "skip" ]]; then
    echo "⚠️  Model ${MODEL} missing and download skipped (FIELDOS_DOWNLOAD_FASTER_WHISPER=skip)." >&2
  else
    echo "📦 Fetching faster-whisper model '${MODEL}'..."
    if ! scripts/download_faster_whisper.sh "${MODEL}"; then
      echo "⚠️  Unable to download model automatically. Rerun with FIELDOS_DOWNLOAD_FASTER_WHISPER=skip once assets are available." >&2
    fi
  fi
fi

echo "🚀 Launching final worker smoke (model=${MODEL}, device=${FIELDOS_WHISPER_DEVICE:-cpu})"
CMD=( "${PYTHON_BIN}" "scripts/start_final_worker.py" "--clip" "${CLIP_PATH}" )
if (( ${#EXTRA_ARGS[@]} > 0 )); then
  CMD+=( "${EXTRA_ARGS[@]}" )
fi
FIELDOS_FINAL_WORKER_ENABLED=true \
FIELDOS_FINAL_WORKER_MOCK="${FIELDOS_FINAL_WORKER_MOCK:-false}" \
FIELDOS_WHISPER_MODEL="${MODEL}" \
KMP_DUPLICATE_LIB_OK=TRUE \
"${CMD[@]}"

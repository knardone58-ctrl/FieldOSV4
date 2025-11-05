#!/usr/bin/env bash
# Download faster-whisper model weights into data/models/faster-whisper/<model>.
# Usage: scripts/download_faster_whisper.sh [model-name]
# Defaults to FIELDOS_WHISPER_MODEL or "base" if not set.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL="${1:-${FIELDOS_WHISPER_MODEL:-base}}"
TARGET_DIR="${ROOT_DIR}/data/models/faster-whisper/${MODEL}"

if [[ -d "${TARGET_DIR}" ]]; then
  echo "✅ Model '${MODEL}' already present at ${TARGET_DIR}."
  exit 0
fi

echo "📦 Downloading faster-whisper model '${MODEL}' into ${TARGET_DIR}" >&2
mkdir -p "${TARGET_DIR}"

if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_BIN="${PYTHON}"
elif [[ -x "${ROOT_DIR}/venv/bin/python" ]]; then
  PYTHON_BIN="${ROOT_DIR}/venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || true)"
fi

if [[ -z "${PYTHON_BIN}" ]]; then
  echo "❌ Unable to locate a Python interpreter. Set PYTHON or create ./venv/bin/python." >&2
  exit 1
fi

MODEL="${MODEL}" TARGET_DIR="${TARGET_DIR}" "${PYTHON_BIN}" - <<'PY'
import os
import sys
from pathlib import Path

model = os.environ.get("MODEL", "base")
target_dir = Path(os.environ["TARGET_DIR"]).resolve()

target_dir.parent.mkdir(parents=True, exist_ok=True)

try:
    from faster_whisper.utils import download_model
except ImportError:
    print(
        "❌ faster-whisper not installed. Activate the repo virtualenv or run scripts/setup_env.sh.",
        file=sys.stderr,
    )
    sys.exit(1)

print(f"➡️  Downloading model '{model}' to {target_dir}", file=sys.stderr)
download_model(model, target_dir)
print("✅ Download complete", file=sys.stderr)
PY

if command -v shasum >/dev/null 2>&1; then
  CHECKSUM=$(find "${TARGET_DIR}" -type f -print0 | sort -z | xargs -0 shasum -a 256 | shasum -a 256 2>/dev/null || true)
  if [[ -n "${CHECKSUM}" ]]; then
    echo "🔐 Aggregate SHA256: ${CHECKSUM}" | sed 's/  -//'
  fi
else
  echo "ℹ️  Install 'shasum' to compute checksums (optional)."
fi

if command -v du >/dev/null 2>&1; then
  echo "📏 Disk usage:"
  du -sh "${TARGET_DIR}"
fi

cat <<INFO
💡 Notes:
  - Models are cached under HUGGINGFACE_HUB_CACHE or HuggingFace defaults. Set the env var to reuse downloads.
  - Refer to https://github.com/guillaumekln/faster-whisper for model catalogue and sizes.
INFO

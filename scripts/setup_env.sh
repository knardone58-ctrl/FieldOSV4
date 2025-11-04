#!/usr/bin/env bash
# FieldOS environment bootstrap

set -euo pipefail

echo "Creating venv..."
python3 -m venv venv
source venv/bin/activate

echo "Installing requirements..."
pip install -U pip
pip install -r requirements.txt

MODEL_NAME="${FIELDOS_WHISPER_MODEL:-base}"
if [[ "${FIELDOS_DOWNLOAD_FASTER_WHISPER:-auto}" != "skip" ]]; then
  echo "Fetching faster-whisper model assets (${MODEL_NAME})..."
  if ! scripts/download_faster_whisper.sh "${MODEL_NAME}"; then
    echo "⚠️  Unable to download faster-whisper model automatically. You can rerun with FIELDOS_DOWNLOAD_FASTER_WHISPER=skip to skip this step." >&2
  fi
else
  echo "Skipping faster-whisper model download (FIELDOS_DOWNLOAD_FASTER_WHISPER=${FIELDOS_DOWNLOAD_FASTER_WHISPER})."
fi

echo "Creating data directories..."
mkdir -p data/audio_cache data/offline_audio_cache qa

echo "Copying example env..."
cp -n .env.example .env 2>/dev/null || true

echo "✅ Environment ready. Activate with: source venv/bin/activate"

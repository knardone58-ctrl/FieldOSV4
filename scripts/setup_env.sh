#!/usr/bin/env bash
# FieldOS environment bootstrap

set -euo pipefail

echo "Creating venv..."
python3 -m venv venv
source venv/bin/activate

echo "Installing requirements..."
pip install -U pip
pip install -r requirements.txt

echo "Creating data directories..."
mkdir -p data/audio_cache data/offline_audio_cache qa

echo "Copying example env..."
cp -n .env.example .env 2>/dev/null || true

echo "✅ Environment ready. Activate with: source venv/bin/activate"

#!/usr/bin/env bash
# Rebuild faster-whisper (and ctranslate2) from source for the local machine.
# Useful when the prebuilt wheels crash due to missing CPU features (e.g., AVX2).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ ! -d "venv" ]]; then
  echo "❌ Missing virtualenv at ${ROOT_DIR}/venv. Run scripts/setup_env.sh first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source "venv/bin/activate"

echo "🧹 Uninstalling prebuilt wheels..."
pip uninstall -y faster-whisper ctranslate2 || true

echo "📦 Installing build prerequisites (requires Homebrew on macOS)..."
if command -v brew >/dev/null 2>&1; then
  brew install cmake ninja pkg-config libomp || true
else
  echo "⚠️  Homebrew not found. Ensure cmake, ninja, pkg-config, and OpenMP are installed manually." >&2
fi

echo "🛠️  Building faster-whisper (source) and ctranslate2 (GitHub) — this may take several minutes..."
export LDFLAGS="-L/usr/local/opt/libomp/lib ${LDFLAGS:-}"
export CPPFLAGS="-I/usr/local/opt/libomp/include ${CPPFLAGS:-}"
pip install --no-binary faster-whisper faster-whisper
pip install git+https://github.com/OpenNMT/CTranslate2.git

echo "✅ Build complete. Rerun scripts/run_final_worker_smoke.sh to validate the worker."

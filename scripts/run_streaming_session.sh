#!/usr/bin/env bash
# Launch the FieldOS Streamlit cockpit for live streaming validation.
# Supports two modes:
#   1. Default: show live Streamlit logs until you press Ctrl+C.
#   2. --tail:   capture the final 80 log lines after exit (good for crash traces).
# After the Streamlit process exits, the script can optionally run the deterministic
# streaming QA test to validate the fallback stub still passes.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -d "venv" ]]; then
  echo "❌ Missing virtualenv at ${ROOT_DIR}/venv. Run scripts/setup_env.sh first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source "venv/bin/activate"

TAIL_MODE=false
RUN_QA=true
PORT="${FIELDOS_STREAMLIT_PORT:-8765}"
ADDRESS="${FIELDOS_STREAMLIT_ADDRESS:-localhost}"

usage() {
  cat <<'EOF'
Usage: scripts/run_streaming_session.sh [options]

Options:
  --tail         Pipe Streamlit logs through 'tail -n 80' (only prints after exit).
  --skip-qa      Skip deterministic streaming QA after Streamlit exits.
  --port <port>  Override Streamlit port (default: 8765 or FIELDOS_STREAMLIT_PORT).
  --address <addr> Override Streamlit bind address (default: localhost or FIELDOS_STREAMLIT_ADDRESS).
  -h, --help     Show this help message.

Examples:
  scripts/run_streaming_session.sh
  scripts/run_streaming_session.sh --tail --skip-qa
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tail)
      TAIL_MODE=true
      shift
      ;;
    --skip-qa)
      RUN_QA=false
      shift
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --address)
      ADDRESS="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

STREAMLIT_CMD=(
  streamlit run app.py
  --server.headless true
  --server.address "${ADDRESS}"
  --server.port "${PORT}"
)

echo "🔌 Starting Streamlit (address=${ADDRESS}, port=${PORT})"
echo "    Press Ctrl+C when you're done exercising the streaming flow."

EXIT_CODE=0
if "${TAIL_MODE}"; then
  set +e
  PYTHONFAULTHANDLER=1 STREAMLIT_BROWSER_GATHER_USAGE_STATS=false "${STREAMLIT_CMD[@]}" 2>&1 | tail -n 80
  PIPE_STATUS=("${PIPESTATUS[@]}")
  EXIT_CODE="${PIPE_STATUS[0]}"
  set -e
else
  PYTHONFAULTHANDLER=1 STREAMLIT_BROWSER_GATHER_USAGE_STATS=false "${STREAMLIT_CMD[@]}"
  EXIT_CODE=$?
fi

if [[ "${EXIT_CODE}" -ne 0 ]]; then
  echo "⚠️  Streamlit exited with status ${EXIT_CODE}."
else
  echo "✅ Streamlit exited cleanly."
fi

if "${RUN_QA}"; then
  echo "🧪 Running deterministic streaming QA (FIELDOS_QA_MODE=true)..."
  FIELDOS_QA_MODE=true python qa/test_fieldos_streaming_deterministic.py
fi

echo "Done. Check data/crm_snapshot.json for updated streaming_stats."

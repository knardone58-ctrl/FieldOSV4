#!/usr/bin/env bash
#
# FieldOS QA environment bootstrapper (macOS-friendly)
# ----------------------------------------------------
# Creates/refreshes a dedicated virtualenv that installs NumPy from source so
# QA suites avoid the macOS OpenBLAS SIGFPE. The rest of the requirements are
# installed on top of that environment, reusing the same versions as your main
# dev setup.
#
# Usage:
#   scripts/setup_qa_env.sh [--run-qa]                    # uses python3, creates ./venv-qa
#   PYTHON=python3.11 scripts/setup_qa_env.sh             # custom interpreter
#   QA_VENV=~/envs/fieldos-qa scripts/setup_qa_env.sh     # custom virtualenv path
#
# After running, activate the env and execute the QA suite:
#   source venv-qa/bin/activate
#   FIELDOS_QA_MODE=true STREAMING_ENABLED=false bash qa/qa_suite.sh
#

set -euo pipefail

RUN_QA=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-qa)
      RUN_QA=true
      shift
      ;;
    *)
      echo "Usage: $0 [--run-qa]" >&2
      exit 2
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

PYTHON_BIN="${PYTHON:-python3}"
QA_VENV_PATH="${QA_VENV:-venv-qa}"

echo "🛠  Preparing FieldOS QA virtualenv"
echo "    Python interpreter : ${PYTHON_BIN}"
echo "    Target virtualenv  : ${QA_VENV_PATH}"
echo

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "❌ Unable to find interpreter '${PYTHON_BIN}' on PATH." >&2
  exit 1
fi

if [[ -d "${QA_VENV_PATH}" ]]; then
  echo "ℹ️  Virtualenv already exists; reusing ${QA_VENV_PATH}"
else
  "${PYTHON_BIN}" -m venv "${QA_VENV_PATH}"
  echo "✅ Created virtualenv ${QA_VENV_PATH}"
fi

# shellcheck disable=SC1090
source "${QA_VENV_PATH}/bin/activate"

echo "🚀 Upgrading pip/setuptools…"
pip install --upgrade pip setuptools wheel >/dev/null

echo "🔁 Installing NumPy from source (no binary wheels)…"
pip install --no-binary=numpy numpy >/dev/null

if [[ -f requirements.txt ]]; then
  echo "📦 Installing project requirements…"
  pip install -r requirements.txt >/dev/null
else
  echo "⚠️  requirements.txt not found, skipping dependency install."
fi

echo "🧩 Ensuring QA dependencies (streamlit)…"
pip install streamlit >/dev/null

echo
echo "✅ FieldOS QA virtualenv ready."
echo "   To use it:"
echo "     source ${QA_VENV_PATH}/bin/activate"
echo "     FIELDOS_QA_MODE=true STREAMING_ENABLED=false bash qa/qa_suite.sh"
echo

if [[ "${RUN_QA}" == "true" ]]; then
  echo "▶️  Running QA suite (FIELDOS_QA_MODE=true STREAMING_ENABLED=false)…"
  FIELDOS_QA_MODE=true STREAMING_ENABLED=false bash qa/qa_suite.sh
fi

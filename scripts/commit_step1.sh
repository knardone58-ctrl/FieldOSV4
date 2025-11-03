#!/usr/bin/env bash
# Automates the Step 1 commit flow:
#  1. Shows current status.
#  2. Stages streaming scaffolding updates (optionally the telemetry snapshot).
#  3. Commits with a default message unless overridden.
#  4. Pushes to origin/main and prints the final status.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

INCLUDE_SNAPSHOT=false
COMMIT_MESSAGE="Harden Vosk streaming path and add headless runner"
REMOTE=origin
BRANCH=main

usage() {
  cat <<'EOF'
Usage: scripts/commit_step1.sh [options]

Options:
  --include-snapshot   Add data/crm_snapshot.json to the commit.
  -m, --message <msg>  Override the default commit message.
  --remote <name>      Push to a different remote (default: origin).
  --branch <name>      Push to a different branch (default: main).
  -h, --help           Show this help text.

Examples:
  scripts/commit_step1.sh
  scripts/commit_step1.sh --include-snapshot -m "Stream telemetry + helper script"
  scripts/commit_step1.sh --remote upstream --branch feature/v4.4-streaming
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --include-snapshot)
      INCLUDE_SNAPSHOT=true
      shift
      ;;
    -m|--message)
      if [[ $# -lt 2 ]]; then
        echo "Missing message for $1" >&2
        exit 1
      fi
      COMMIT_MESSAGE="$2"
      shift 2
      ;;
    --remote)
      REMOTE="$2"
      shift 2
      ;;
    --branch)
      BRANCH="$2"
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

echo "📂 Repository: $ROOT_DIR"
echo "🔍 Current status:"
git status

TO_STAGE=(
  streaming_asr.py
  scripts/run_streaming_session.sh
)

if "${INCLUDE_SNAPSHOT}"; then
  if [[ -f "data/crm_snapshot.json" ]]; then
    TO_STAGE+=("data/crm_snapshot.json")
  else
    echo "⚠️  data/crm_snapshot.json not found; skipping snapshot."
  fi
fi

echo "➕ Staging files:"
for path in "${TO_STAGE[@]}"; do
  if [[ -e "$path" ]]; then
    echo "   git add $path"
    git add "$path"
  else
    echo "   ⚠️  Skipping missing path: $path"
  fi
done

echo "🔎 Staged diff summary:"
git status --short

echo "📝 Committing: \"$COMMIT_MESSAGE\""
git commit -m "$COMMIT_MESSAGE"

echo "🚀 Pushing to $REMOTE/$BRANCH"
git push "$REMOTE" "$BRANCH"

echo "✅ Final status:"
git status

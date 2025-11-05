#!/usr/bin/env bash
# Commit and push timeline updates (timeline.json + product_timeline.md).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMMIT_MESSAGE="Log streaming pilot milestone"
REMOTE="origin"
BRANCH="main"
PUSH=true

usage() {
  cat <<'EOF'
Usage: scripts/commit_timeline_update.sh [options]

Options:
  -m, --message <msg>   Commit message (default: "Log streaming pilot milestone")
  --remote <name>       Remote to push (default: origin)
  --branch <name>       Branch to push (default: main)
  --no-push             Skip git push (commit only)
  -h, --help            Show this help text.

Examples:
  scripts/commit_timeline_update.sh
  scripts/commit_timeline_update.sh -m "Document V4.4 pilot prep" --no-push
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -m|--message)
      if [[ $# -lt 2 ]]; then
        echo "Missing argument for $1" >&2
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
    --no-push)
      PUSH=false
      shift
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

echo "📁 Repo: $ROOT_DIR"
echo "🔍 Current status:"
git status --short docs/fieldos_narrative/timeline.json docs/fieldos_narrative/product_timeline.md || true

echo "➕ Staging timeline files..."
git add docs/fieldos_narrative/timeline.json docs/fieldos_narrative/product_timeline.md

echo "📝 Committing with message: \"$COMMIT_MESSAGE\""
git commit -m "$COMMIT_MESSAGE"

if "$PUSH"; then
  echo "🚀 Pushing to $REMOTE/$BRANCH"
  git push "$REMOTE" "$BRANCH"
else
  echo "⚠️  Skipping push (requested)."
fi

echo "✅ Done."

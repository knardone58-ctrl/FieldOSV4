#!/usr/bin/env bash
# Helper for post-CI follow-up: review telemetry artifacts, optionally tag release,
# and jot down next milestones.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

TAG_NAME=""
TAG_MESSAGE=""
TODO_FILE=""

usage() {
  cat <<'EOF'
Usage: scripts/post_ci_wrap.sh [options]

Options:
  --tag <version>         Create a git tag (e.g., v4.4.0-alpha) after checks.
  --tag-message <msg>     Annotation message for the tag (defaults to auto text).
  --todo-file <path>      Append next-step bullets to the specified file.
  -h, --help              Show this help message.

Examples:
  scripts/post_ci_wrap.sh
  scripts/post_ci_wrap.sh --tag v4.4.0-alpha
  scripts/post_ci_wrap.sh --tag v4.4.0-alpha --todo-file docs/next_milestones.md
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)
      TAG_NAME="$2"
      shift 2
      ;;
    --tag-message)
      TAG_MESSAGE="$2"
      shift 2
      ;;
    --todo-file)
      TODO_FILE="$2"
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

echo "🗂  Checking telemetry artifacts..."

if [[ -f "scripts/report_ops_log.py" ]]; then
  python3 scripts/report_ops_log.py || echo "⚠️  Ops log report failed."
else
  echo "⚠️  scripts/report_ops_log.py not found."
fi

if [[ -f "qa/last_whisper_accuracy.json" ]]; then
  python3 - <<'PY'
import json, pathlib
path = pathlib.Path("qa/last_whisper_accuracy.json")
data = json.loads(path.read_text())
print("\n🎯 Whisper accuracy snapshot:")
for idx, row in enumerate(data, start=1):
    print(f"  {idx}. {row['label']:<20} latency={row['latency_s']:.1f}s conf={row['confidence']:.2f} ai_fail={row['ai_fail']}")
PY
else
  echo "⚠️  qa/last_whisper_accuracy.json not found."
fi

echo

if [[ -n "$TAG_NAME" ]]; then
  if git rev-parse "$TAG_NAME" >/dev/null 2>&1; then
    echo "⚠️  Tag $TAG_NAME already exists. Skipping."
  else
    msg="${TAG_MESSAGE:-"FieldOS release $TAG_NAME"}"
    git tag -a "$TAG_NAME" -m "$msg"
    git push origin "$TAG_NAME"
    echo "✅ Created and pushed tag $TAG_NAME"
  fi
fi

if [[ -n "$TODO_FILE" ]]; then
  mkdir -p "$(dirname "$TODO_FILE")"
  {
    echo "- [ ] Stand up ops dashboard enhancements (streaming trend lines, filters)."
    echo "- [ ] Capture streaming config toggles for pilot rollout (document `.env` expectations)."
    echo "- [ ] Schedule pilot telemetry review (check ops_log.jsonl trends)."
  } >> "$TODO_FILE"
  echo "📝 Added next steps to $TODO_FILE"
fi

echo "Done."

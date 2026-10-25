#!/usr/bin/env bash
# Push wiki/ markdown files to GitHub Wiki for ZIZKA-AI-SL/ZizkaDB.
#
# Prerequisites:
#   1. Enable Wiki: GitHub repo → Settings → Features → Wikis ✓
#   2. git credentials for push access to the repo
#
# Usage: bash wiki/push-wiki.sh

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WIKI_DIR="${WIKI_CLONE_DIR:-/tmp/ZizkaDB.wiki}"
REPO="https://github.com/ZIZKA-AI-SL/ZizkaDB.wiki.git"

echo "▶ Cloning or updating wiki repo..."
if [[ -d "$WIKI_DIR/.git" ]]; then
  git -C "$WIKI_DIR" pull origin master 2>/dev/null || git -C "$WIKI_DIR" pull origin main 2>/dev/null || true
else
  rm -rf "$WIKI_DIR"
  if ! git clone "$REPO" "$WIKI_DIR"; then
    echo ""
    echo "ERROR: Could not clone $REPO"
    echo "Enable Wikis first: https://github.com/ZIZKA-AI-SL/ZizkaDB/settings"
    echo "  → Features → Wikis → check 'Wikis'"
    echo "Then run this script again."
    exit 1
  fi
fi

echo "▶ Copying wiki pages..."
rsync -av --delete \
  --exclude '.git' \
  --exclude 'push-wiki.sh' \
  --exclude 'README.md' \
  "$ROOT/wiki/" "$WIKI_DIR/"

cd "$WIKI_DIR"
git add -A
if git diff --staged --quiet; then
  echo "✅ Wiki already up to date."
  exit 0
fi

git commit -m "Update wiki from main repo ($(date -Iseconds))"
BRANCH="$(git branch --show-current 2>/dev/null || echo master)"
git push origin "$BRANCH"
echo "✅ Wiki pushed: https://github.com/ZIZKA-AI-SL/ZizkaDB/wiki"

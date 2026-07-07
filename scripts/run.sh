#!/usr/bin/env bash
# Cron entry point: sync new PDFs into references.json, then commit & push so the
# GitHub Pages site updates. Designed to be safe to run repeatedly (it no-ops
# when nothing changed).
#
# Usage:  scripts/run.sh [extra args passed to papersync.sync]
# Logs to: papersimreading.log next to the repo (git-ignored).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

LOG="$REPO_DIR/papersimreading.log"
exec >>"$LOG" 2>&1
echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) run start ====="

# Make sure the local Dropbox copy is current before scanning (best effort).
command -v dropbox >/dev/null 2>&1 && dropbox status || true

python3 -m papersync.sync "$@"

if [[ -n "$(git status --porcelain data/references.json)" ]]; then
  git add data/references.json
  n=$(git diff --cached --numstat data/references.json | awk '{print $1}')
  git commit -m "Update references ($(date -u +%Y-%m-%d))" -m "Automated sync from Dropbox folder."
  git push origin HEAD
  echo "pushed changes"
else
  echo "no changes"
fi
echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) run end ====="

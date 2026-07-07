#!/usr/bin/env bash
# Install (or refresh) a crontab entry that runs the sync daily.
# Idempotent: re-running replaces the existing papersimreading entry.
#
#   scripts/install-cron.sh            # default: daily at 07:30
#   CRON_SCHEDULE="0 * * * *" scripts/install-cron.sh   # hourly
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEDULE="${CRON_SCHEDULE:-30 7 * * *}"
MARKER="# papersimreading-sync"
LINE="$SCHEDULE cd $REPO_DIR && /usr/bin/env bash scripts/run.sh $MARKER"

# Preserve all other crontab lines; drop any prior papersimreading entry.
existing="$(crontab -l 2>/dev/null | grep -v "$MARKER" || true)"
printf '%s\n%s\n' "$existing" "$LINE" | sed '/^$/d' | crontab -

echo "Installed cron entry:"
echo "  $LINE"
echo
echo "Current crontab:"
crontab -l

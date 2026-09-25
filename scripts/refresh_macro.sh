#!/usr/bin/env bash
# Daily macro refresh. Plan.md §4.7, §20.6.
#
# Run from cron, not from the interface: §20.7 requires the system to work
# headless, so a collection job that only runs when someone opens a page is one
# that stops the day nobody does.
#
# Idempotent — the ingestor inserts the days that are missing and skips the rest
# — so a double run costs a few API calls and changes nothing. That matters more
# than it sounds: a job that must not run twice is one nobody dares retry.
set -euo pipefail

cd "$(dirname "$0")/.."

# The archive is reached from the host here, not from inside the compose
# network, so the service name in .env has to be overridden.
export POSTGRES_HOST="${POSTGRES_HOST:-localhost}"

LOG="${TRADELAB_LOG_DIR:-$PWD/logs}/macro.log"
mkdir -p "$(dirname "$LOG")"

{
  echo "--- $(date -Is) refresh starting"
  .venv/bin/python scripts/ingest_macro.py --since "$(date -d '90 days ago' +%F)"
  echo "--- $(date -Is) refresh finished"
} >> "$LOG" 2>&1

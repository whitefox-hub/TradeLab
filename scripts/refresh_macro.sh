#!/usr/bin/env bash
# Daily macro refresh, run from cron (headless per §20.7). Plan.md §4.7, §20.6.
# Idempotent: the ingestor inserts only missing days, so retries are safe.
set -euo pipefail

cd "$(dirname "$0")/.."

# Runs on the host, not in the compose network, so override the .env service name.
export POSTGRES_HOST="${POSTGRES_HOST:-localhost}"

LOG="${TRADELAB_LOG_DIR:-$PWD/logs}/macro.log"
mkdir -p "$(dirname "$LOG")"

{
  echo "--- $(date -Is) refresh starting"
  .venv/bin/python scripts/ingest_macro.py --since "$(date -d '90 days ago' +%F)"
  echo "--- $(date -Is) refresh finished"
} >> "$LOG" 2>&1

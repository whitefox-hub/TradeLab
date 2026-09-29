#!/usr/bin/env bash
# Start or stop everything the operator runs: docker (database, capture), the app on
# port 8000 and the forward trend book.
#   ./tradelab.sh start
#   ./tradelab.sh stop
#   ./tradelab.sh status
set -euo pipefail
cd "$(dirname "$0")"

running() { [[ -f "logs/$1.pid" ]] && kill -0 "$(cat "logs/$1.pid")" 2>/dev/null; }

case "${1:-}" in
  start)
    ./scripts/start_services.sh
    ;;
  stop)
    ./scripts/start_services.sh stop
    echo "stopping docker (capture flushes its buffer first, up to 90 s)..."
    docker compose stop
    ;;
  status)
    docker compose ps --format '{{.Service}}: {{.Status}}'
    for name in uvicorn trend_book; do
      if running "$name"; then echo "$name: running, pid $(cat "logs/$name.pid")"
      else echo "$name: stopped"; fi
    done
    ;;
  *)
    echo "usage: ./tradelab.sh start|stop|status" >&2
    exit 1
    ;;
esac

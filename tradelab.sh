#!/usr/bin/env bash
# Start or stop everything the operator runs: docker (database, capture), the app on
# http://127.0.0.1:8010 and the forward trend book.
#   ./tradelab.sh start
#   ./tradelab.sh stop
#   ./tradelab.sh status
set -euo pipefail
cd "$(dirname "$0")"

running() { [[ -f "logs/$1.pid" ]] && kill -0 "$(cat "logs/$1.pid")" 2>/dev/null; }

case "${1:-}" in
  start)
    ./scripts/start_services.sh
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      curl -sf -o /dev/null http://127.0.0.1:8010/home && break
      sleep 1
    done
    if curl -sf -o /dev/null http://127.0.0.1:8010/home; then echo "app: http://127.0.0.1:8010/home"
    else echo "app did not answer; see logs/uvicorn.log" >&2; exit 1; fi
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
    curl -sf -o /dev/null http://127.0.0.1:8010/home && echo "app: http://127.0.0.1:8010/home" \
      || echo "app: not answering on 8010"
    ;;
  *)
    echo "usage: ./tradelab.sh start|stop|status" >&2
    exit 1
    ;;
esac

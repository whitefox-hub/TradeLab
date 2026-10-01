#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs

stop() {
  local pidfile="logs/$1.pid"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    kill "$(cat "$pidfile")"
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$(cat "$pidfile")" 2>/dev/null || break
      sleep 1
    done
  fi
  rm -f "$pidfile"
}

start() {
  local name="$1"; shift
  setsid nohup "$@" >> "logs/$name.log" 2>&1 < /dev/null &
  echo $! > "logs/$name.pid"
  echo "$name started, pid $(cat "logs/$name.pid"), log logs/$name.log"
}

stop uvicorn
stop btc_ladder
[[ "${1:-}" == "stop" ]] && exit 0

docker compose up -d capture
set -a; . secrets/api.env; set +a
if ss -ltn | grep -q "127.0.0.1:8010 "; then
  echo "port 8010 is taken by another program; the app cannot start" >&2
  exit 1
fi
start uvicorn .venv/bin/uvicorn "tradelab.api.app:create_app" --factory \
  --host 127.0.0.1 --port 8010
start btc_ladder .venv/bin/python scripts/run_btc_ladder.py

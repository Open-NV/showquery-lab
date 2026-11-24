#!/usr/bin/env bash
# Start/stop the local showquery stack: mongod -> osqueryd -> extension -> API.
#
#   scripts/stack.sh up | down | status | logs
#
# Environment overrides:
#   OSQUERYD                path to osqueryd (macOS default: /opt/osquery/lib/osquery.app/...)
#   MONGOD                  path to mongod
#   SHOWQUERY_MANAGE_MONGO  0 to use an existing MongoDB (e.g. Docker) at SHOWQUERY_MONGO_URI
#   SHOWQUERY_RUN_DIR       sockets, pids, logs and mongo data (default: ./.run)
#   PYTHON                  interpreter with showquery installed (default: ./.venv/bin/python)
#   API_PORT                default 8000
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${SHOWQUERY_RUN_DIR:-$ROOT/.run}"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"
MAC_OSQUERYD=/opt/osquery/lib/osquery.app/Contents/MacOS/osqueryd
OSQUERYD="${OSQUERYD:-$(command -v osqueryd || echo "$MAC_OSQUERYD")}"
MONGOD="${MONGOD:-$(command -v mongod || true)}"
MANAGE_MONGO="${SHOWQUERY_MANAGE_MONGO:-1}"
API_PORT="${API_PORT:-8000}"
export SHOWQUERY_OSQUERY_SOCKET="$RUN_DIR/osquery.sock"
SERVICES=(mongod osqueryd extension api)

log() { printf '\033[1m[stack]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[stack]\033[0m %s\n' "$*" >&2; exit 1; }

pid_of() { [[ -f "$RUN_DIR/$1.pid" ]] && cat "$RUN_DIR/$1.pid" || true; }
running() { local p; p="$(pid_of "$1")"; [[ -n "$p" ]] && kill -0 "$p" 2>/dev/null; }

start_bg() {  # name cmd...
  local name="$1"; shift
  if running "$name"; then log "$name already running (pid $(pid_of "$name"))"; return; fi
  nohup "$@" >"$RUN_DIR/$name.log" 2>&1 &
  echo $! >"$RUN_DIR/$name.pid"
  log "started $name (pid $!), log: $RUN_DIR/$name.log"
}

wait_until() {  # description seconds cmd...
  local what="$1" secs="$2"; shift 2
  for _ in $(seq 1 $((secs * 4))); do "$@" >/dev/null 2>&1 && return 0; sleep 0.25; done
  die "timed out waiting for $what (see logs in $RUN_DIR)"
}

mongo_ok() { "$PYTHON" -c "from pymongo import MongoClient; from showquery.config import get_settings as s; MongoClient(s().mongo_uri, serverSelectionTimeoutMS=500).admin.command('ping')"; }
tables_ok() { "$PYTHON" -c "
from showquery.osquery_client import OsqueryClient
OsqueryClient('$SHOWQUERY_OSQUERY_SOCKET').query('SELECT 1 FROM show_commands WHERE 0')"; }
api_ok() { curl -fsS "http://127.0.0.1:$API_PORT/api/health"; }

up() {
  mkdir -p "$RUN_DIR"
  if [[ "$MANAGE_MONGO" == 1 ]]; then
    [[ -n "$MONGOD" && -x "$MONGOD" ]] || die "mongod not found; set MONGOD or SHOWQUERY_MANAGE_MONGO=0"
    mkdir -p "$RUN_DIR/mongo"
    start_bg mongod "$MONGOD" --dbpath "$RUN_DIR/mongo" --bind_ip 127.0.0.1 --port 27017 --quiet
  fi
  wait_until "MongoDB" 30 mongo_ok

  [[ -x "$OSQUERYD" ]] || die "osqueryd not found; set OSQUERYD"
  local enabled
  enabled="$("$PYTHON" -c 'from showquery.catalog import enabled_tables_flag as f; print(f())')"
  mkdir -p "$RUN_DIR/osquery.db"
  start_bg osqueryd "$OSQUERYD" --flagfile="$ROOT/config/osquery.flags" \
    --extensions_socket="$SHOWQUERY_OSQUERY_SOCKET" \
    --database_path="$RUN_DIR/osquery.db" --pidfile="$RUN_DIR/osqueryd.osquery-pid" \
    --enable_tables="$enabled"
  wait_until "osquery socket" 20 test -S "$SHOWQUERY_OSQUERY_SOCKET"

  start_bg extension "$PYTHON" -m showquery.extension --socket "$SHOWQUERY_OSQUERY_SOCKET" --timeout 5
  wait_until "extension tables" 20 tables_ok

  start_bg api "$PYTHON" -m showquery.api --port "$API_PORT"
  wait_until "API" 20 api_ok
  log "ready: API http://127.0.0.1:$API_PORT  (UI dev server: cd web && npm run dev)"
}

down() {
  local i name p
  for ((i=${#SERVICES[@]}-1; i>=0; i--)); do
    name="${SERVICES[$i]}"
    if running "$name"; then
      p="$(pid_of "$name")"; kill "$p" 2>/dev/null || true
      for _ in $(seq 1 40); do kill -0 "$p" 2>/dev/null || break; sleep 0.25; done
      kill -9 "$p" 2>/dev/null || true
      log "stopped $name"
    fi
    rm -f "$RUN_DIR/$name.pid"
  done
}

status() {
  for name in "${SERVICES[@]}"; do
    if running "$name"; then echo "$name: running (pid $(pid_of "$name"))"; else echo "$name: stopped"; fi
  done
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  status) status ;;
  logs) tail -n 40 "$RUN_DIR"/*.log ;;
  *) echo "usage: $0 up|down|status|logs" >&2; exit 2 ;;
esac

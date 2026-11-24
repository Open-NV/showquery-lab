#!/usr/bin/env bash
# Runs osqueryd, the showquery extension and the API in one container.
# If any of them exits, the container exits so the restart policy can recover it.
set -euo pipefail

RUN_DIR="${SHOWQUERY_RUN_DIR:-/run/showquery}"
SOCKET="$SHOWQUERY_OSQUERY_SOCKET"
mkdir -p "$RUN_DIR/osquery.db"
rm -f "$SOCKET"* 2>/dev/null || true

log() { echo "[entrypoint] $*"; }

enabled="$(python -c 'from showquery.catalog import enabled_tables_flag as f; print(f())')"
"$OSQUERYD" --flagfile=/app/config/osquery.flags \
  --extensions_socket="$SOCKET" \
  --database_path="$RUN_DIR/osquery.db" --pidfile="$RUN_DIR/osqueryd.pid" \
  --enable_tables="$enabled" &
log "osqueryd started (tables: $enabled)"

for _ in $(seq 1 80); do [[ -S "$SOCKET" ]] && break; sleep 0.25; done
[[ -S "$SOCKET" ]] || { log "osquery socket never appeared"; exit 1; }

python -m showquery.extension --socket "$SOCKET" --timeout 5 &
log "extension started"

for _ in $(seq 1 80); do
  python -c "from showquery.osquery_client import OsqueryClient; OsqueryClient('$SOCKET').query('SELECT 1 FROM show_commands WHERE 0')" \
    >/dev/null 2>&1 && break
  sleep 0.25
done

if [[ "${SHOWQUERY_SEED_IF_EMPTY:-1}" == 1 ]]; then
  python -m showquery.seed --days "${SHOWQUERY_SEED_DAYS:-30}" --if-empty
fi

python -m showquery.api --host 0.0.0.0 --port 8000 &
log "API listening on :8000"

wait -n
log "a process exited; stopping container"
exit 1

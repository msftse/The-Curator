#!/usr/bin/env bash
# M5 end-to-end smoke (M5-8).
#
# Brings up the local emulator stack (Cosmos + Azurite + Redis), waits
# for each service to be reachable on its conventional port, runs the
# M5 full-flow E2E suite, then tears the stack back down on success.
#
# Usage:   ./scripts/smoke_m5.sh
# Env:     KEEP_STACK=1     — skip the final `docker compose down`.
#          NO_DOWN_ON_FAIL=1 (default) — never tear down on failure so the
#                            operator can inspect emulator logs.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

KEEP_STACK="${KEEP_STACK:-0}"

log() { printf '\033[1;36m[smoke-m5]\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m[ ok ]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[fail]\033[0m %s\n' "$*" >&2; }

cleanup_failure() {
  err "smoke failed — leaving stack up for inspection."
  err "run \`docker compose down\` manually when you're done."
}

wait_for_port() {
  local host="$1" port="$2" name="$3" max="${4:-90}"
  log "waiting for $name on $host:$port (up to ${max}s)..."
  local i=0
  while (( i < max )); do
    if nc -z "$host" "$port" 2>/dev/null; then
      ok "$name reachable on :$port"
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  err "$name never became reachable on :$port"
  return 1
}

log "starting docker compose stack..."
docker compose up -d

# Cosmos emulator takes the longest — give it a 3 min ceiling.
wait_for_port localhost 8081  cosmos   180 || { cleanup_failure; exit 1; }
wait_for_port localhost 10000 azurite  60  || { cleanup_failure; exit 1; }
wait_for_port localhost 6379  redis    30  || { cleanup_failure; exit 1; }

# Extra grace: cosmos accepts TCP before the data plane is queryable.
log "warm-up sleep (10s) so the cosmos data plane finishes initialising..."
sleep 10

log "running E2E suite..."
set +e
pytest backend/tests/e2e/test_m5_full_flow.py -v
rc=$?
set -e

if [[ $rc -eq 0 ]]; then
  ok "M5 full-flow E2E: PASS"
  if [[ "$KEEP_STACK" != "1" ]]; then
    log "tearing stack down..."
    docker compose down
  else
    log "KEEP_STACK=1 — leaving stack up."
  fi
  exit 0
else
  err "M5 full-flow E2E: FAIL (pytest exit $rc)"
  cleanup_failure
  exit "$rc"
fi

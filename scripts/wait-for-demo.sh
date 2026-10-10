#!/usr/bin/env bash
# Wait until the seeded demo tenant has been imported and analyzed (anomalies exist).
# Usage: scripts/wait-for-demo.sh [base_url] [timeout_seconds]
set -euo pipefail
BASE="${1:-http://127.0.0.1:${NIMBRIX_WEB_PORT:-3200}}"
TIMEOUT="${2:-180}"
PASSWORD="${NIMBRIX_DEMO_PASSWORD:-nimbrix-demo-password}"
JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT

deadline=$((SECONDS + TIMEOUT))
until curl -fsS -c "$JAR" -H 'content-type: application/json' \
    -d "{\"email\":\"analyst@acme.demo\",\"password\":\"${PASSWORD}\"}" "$BASE/api/v1/auth/login" >/dev/null 2>&1; do
  if (( SECONDS > deadline )); then echo "demo users not seeded after ${TIMEOUT}s" >&2; exit 1; fi
  sleep 2
done
until [[ "$(curl -fsS -b "$JAR" "$BASE/api/v1/anomalies?limit=1" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)["items"]))')" -gt 0 ]]; do
  if (( SECONDS > deadline )); then echo "demo analysis not finished after ${TIMEOUT}s" >&2; exit 1; fi
  sleep 2
done
echo "demo data ready"

#!/bin/sh
set -eu

base_url="${ASSISTANT_URL:-http://localhost:8000}"
curl --fail --silent "$base_url/health" | grep '"status":"ok"' >/dev/null
curl --fail --silent "$base_url/api/calendar/today" | grep 'Upstart interview' >/dev/null
printf 'Friday smoke test passed: %s\n' "$base_url"


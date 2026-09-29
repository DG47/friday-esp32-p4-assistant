#!/bin/sh
set -eu

base_url="${ASSISTANT_URL:-http://localhost:8000}"
read_token="${ASSISTANT_READ_TOKEN:-${ASSISTANT_SIMULATOR_TOKEN:-}}"
if [ -z "$read_token" ]; then
  echo "Set ASSISTANT_READ_TOKEN or ASSISTANT_SIMULATOR_TOKEN for private API checks" >&2
  exit 1
fi

curl_friday() {
  if [ -n "${ASSISTANT_CA_CERT:-}" ]; then
    curl --cacert "$ASSISTANT_CA_CERT" "$@"
  else
    curl "$@"
  fi
}

curl_friday --fail --silent "$base_url/health" | grep '"status":"ok"' >/dev/null
curl_friday --fail --silent \
  -H "X-Friday-Simulator-Token: $read_token" \
  "$base_url/api/calendar/today" | grep 'Upstart interview' >/dev/null
printf 'Friday smoke test passed: %s\n' "$base_url"

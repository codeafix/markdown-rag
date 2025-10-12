#!/usr/bin/env bash
set -euo pipefail

API_URL="http://localhost:8000"
HEALTH_ENDPOINT="$API_URL/health"
STREAM_ENDPOINT="$API_URL/query/stream"

have_cmd() { command -v "$1" >/dev/null 2>&1; }

bring_up() {
  echo "Bringing up containers..."
  if have_cmd podman && podman compose version >/dev/null 2>&1; then
    podman compose -f docker-compose.yml up -d --build
  elif have_cmd docker && docker compose version >/dev/null 2>&1; then
    docker compose -f docker-compose.yml up -d --build
  else
    echo "Neither podman nor docker compose found. Install one and try again." >&2
    exit 1
  fi
}

wait_health() {
  echo "Waiting for API health at $HEALTH_ENDPOINT ..."
  for i in {1..120}; do
    if curl -sf "$HEALTH_ENDPOINT" >/dev/null; then
      echo "API is healthy."
      return 0
    fi
    sleep 1
  done
  echo "Timed out waiting for API health." >&2
  return 1
}

ensure_up() {
  echo "Looking for API at $HEALTH_ENDPOINT ..."
  if ! curl -sf "$HEALTH_ENDPOINT" >/dev/null; then
    bring_up
    wait_health
  else
    echo "API already running."
  fi
}

chat_loop() {
  echo "Type your questions. Press Ctrl+C to exit."
  while true; do
    read -r -p "Q: " Q || break
    if [[ -z "${Q}" ]]; then
      continue
    fi
    echo "---"
    curl --no-buffer -s -X POST "$STREAM_ENDPOINT" \
      -H "Content-Type: application/json" \
      -d "$(jq -n --arg q "$Q" '{question:$q}')" || true
    echo -e "\n---"
  done
}

main() {
  ensure_up
  chat_loop
}

main "$@"

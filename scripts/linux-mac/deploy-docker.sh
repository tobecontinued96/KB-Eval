#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$ROOT_DIR"

NO_BUILD=0
PULL=0
DOWN=0
LOGS=0
NO_OPEN=0

usage() {
  cat <<'EOF'
Usage: ./deploy-docker.sh [options]

Options:
  --no-build   Start existing images without rebuilding.
  --pull       Pull the Postgres image before starting.
  --down       Stop the Docker Compose services.
  --logs       Follow backend/frontend logs after startup.
  --no-open    Do not open the browser after startup.
  -h, --help   Show this help.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --no-build) NO_BUILD=1 ;;
    --pull) PULL=1 ;;
    --down) DOWN=1 ;;
    --logs) LOGS=1 ;;
    --no-open) NO_OPEN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[x] Unknown argument: $arg" >&2; usage; exit 2 ;;
  esac
done

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

read_compose_value() {
  local name="$1"
  local default="$2"
  local value="${!name:-}"
  local line

  if [[ -n "$value" ]]; then
    printf '%s\n' "$value"
    return
  fi

  if [[ -f ".env" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%%#*}"
      if [[ "$line" =~ ^[[:space:]]*${name}[[:space:]]*=[[:space:]]*(.+)[[:space:]]*$ ]]; then
        value="${BASH_REMATCH[1]}"
        value="${value%\"}"
        value="${value#\"}"
        value="${value%\'}"
        value="${value#\'}"
        printf '%s\n' "$value"
        return
      fi
    done < ".env"
  fi

  printf '%s\n' "$default"
}

open_browser() {
  local url="$1"
  if [[ "$NO_OPEN" -eq 1 ]]; then
    return
  fi
  if command_exists xdg-open; then
    xdg-open "$url" >/dev/null 2>&1 || true
  elif command_exists open; then
    open "$url" >/dev/null 2>&1 || true
  else
    echo "[i] Open this URL in your browser: $url"
  fi
}

wait_for_health() {
  local url="$1"
  local i
  for ((i = 1; i <= 60; i++)); do
    if command_exists curl && curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

if ! command_exists docker; then
  echo "[x] docker was not found. Install Docker or add it to PATH." >&2
  exit 1
fi

if [[ "$DOWN" -eq 1 ]]; then
  echo "==> Stopping Docker services"
  docker compose down
  exit $?
fi

mkdir -p datasets/generated reports logs generated_sources config

if [[ "$PULL" -eq 1 ]]; then
  echo "==> Pulling base services"
  docker compose pull db
fi

compose_args=(compose up -d)
if [[ "$NO_BUILD" -eq 0 ]]; then
  compose_args+=(--build)
fi
compose_args+=(db backend frontend)

echo "==> docker ${compose_args[*]}"
docker "${compose_args[@]}"

frontend_port="$(read_compose_value FRONTEND_PORT 5598)"
url="http://127.0.0.1:${frontend_port}"
health_url="${url}/api/health"

echo "==> Waiting for health check: ${health_url}"
if wait_for_health "$health_url"; then
  echo "[+] Dify-KB-Eval is ready: ${url}"
  open_browser "$url"
else
  echo "[!] Health check did not pass within 60 seconds. Run 'docker compose logs backend frontend' for details." >&2
fi

cat <<EOF

Useful commands:
  docker compose ps
  docker compose logs -f backend frontend
  ./scripts/linux-mac/deploy-docker.sh --down
EOF

if [[ "$LOGS" -eq 1 ]]; then
  docker compose logs -f backend frontend
fi

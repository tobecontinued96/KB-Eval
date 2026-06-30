#!/usr/bin/env bash
set -Eeuo pipefail

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PACKAGE_DIR"

DOWN=0
LOGS=0
NO_OPEN=0

usage() {
  cat <<'EOF'
Usage: ./deploy-offline.sh [options]

Options:
  --down      Stop the offline Docker Compose services.
  --logs      Follow backend/frontend logs after startup.
  --no-open   Do not open the browser after startup.
  -h, --help  Show this help.
EOF
}

for arg in "$@"; do
  case "$arg" in
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

read_env_value() {
  local name="$1"
  local default="$2"
  local value=""
  local line
  if [[ -f ".env.offline" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%%#*}"
      if [[ "$line" =~ ^[[:space:]]*${name}[[:space:]]*=[[:space:]]*(.*)[[:space:]]*$ ]]; then
        value="${BASH_REMATCH[1]}"
        value="${value%\"}"
        value="${value#\"}"
        value="${value%\'}"
        value="${value#\'}"
        printf '%s\n' "$value"
        return
      fi
    done < ".env.offline"
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
  for ((i = 1; i <= 90; i++)); do
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

compose_args=(compose --project-name dify-kb-eval --env-file .env.offline -f docker-compose.offline.yml)

if [[ "$DOWN" -eq 1 ]]; then
  echo "==> Stopping offline services"
  docker "${compose_args[@]}" down
  exit $?
fi

for required in .env.offline docker-compose.offline.yml images; do
  if [[ ! -e "$required" ]]; then
    echo "[x] Missing $required in $PACKAGE_DIR" >&2
    exit 1
  fi
done

for image_tar in images/*.tar; do
  [[ -f "$image_tar" ]] || continue
  echo "==> Loading image $(basename "$image_tar")"
  docker load -i "$image_tar"
done

mkdir -p datasets reports logs generated_sources config docs

echo "==> Starting offline services"
docker "${compose_args[@]}" up -d

frontend_port="$(read_env_value FRONTEND_PORT 5598)"
url="http://127.0.0.1:${frontend_port}"
health_url="${url}/api/health"

echo "==> Waiting for health check: ${health_url}"
if wait_for_health "$health_url"; then
  echo "[+] Dify-KB-Eval is ready: ${url}"
  open_browser "$url"
else
  echo "[!] Health check did not pass within 90 seconds. Check logs with docker compose." >&2
fi

cat <<EOF

Useful commands:
  docker compose --project-name dify-kb-eval --env-file .env.offline -f docker-compose.offline.yml ps
  docker compose --project-name dify-kb-eval --env-file .env.offline -f docker-compose.offline.yml logs -f backend frontend
  ./deploy-offline.sh --down
EOF

if [[ "$LOGS" -eq 1 ]]; then
  docker "${compose_args[@]}" logs -f backend frontend
fi

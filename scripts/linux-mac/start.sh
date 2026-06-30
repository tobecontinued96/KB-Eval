#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$ROOT_DIR"

MOCK_MODE=0
for arg in "$@"; do
  case "$arg" in
    --mock|-m|-Mock)
      MOCK_MODE=1
      ;;
    -h|--help)
      cat <<'EOF'
Usage: ./start.sh [--mock]

Starts Dify-KB-Eval backend and frontend in the current terminal.
Use --mock to start only the frontend with VITE_USE_MOCK=true.
EOF
      exit 0
      ;;
    *)
      echo "[x] Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

read_dev_port() {
  local port="5598"
  local file line value
  for file in "$ROOT_DIR/frontend/.env" "$ROOT_DIR/frontend/.env.local"; do
    [[ -f "$file" ]] || continue
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%%#*}"
      if [[ "$line" =~ ^[[:space:]]*DEV_PORT[[:space:]]*=[[:space:]]*([0-9]+)[[:space:]]*$ ]]; then
        value="${BASH_REMATCH[1]}"
        port="$value"
      fi
    done < "$file"
  done
  printf '%s\n' "$port"
}

is_port_open() {
  local port="$1"
  if command_exists lsof; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi
  if command_exists nc; then
    nc -z 127.0.0.1 "$port" >/dev/null 2>&1
    return $?
  fi
  (echo >/dev/tcp/127.0.0.1/"$port") >/dev/null 2>&1
}

open_browser() {
  local url="$1"
  if command_exists xdg-open; then
    xdg-open "$url" >/dev/null 2>&1 || true
  elif command_exists open; then
    open "$url" >/dev/null 2>&1 || true
  else
    echo "[i] Open this URL in your browser: $url"
  fi
}

wait_for_url() {
  local url="$1"
  local tries="${2:-30}"
  local i
  for ((i = 1; i <= tries; i++)); do
    if command_exists curl && curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

FRONTEND_PORT="$(read_dev_port)"
BACKEND_PORT=8200
BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
FRONTEND_URL="http://127.0.0.1:${FRONTEND_PORT}"
HEALTH_URL="${BACKEND_URL}/api/health"

echo
echo "==> Dify-KB-Eval start"
echo "    Backend:  ${BACKEND_URL}"
echo "    Frontend: ${FRONTEND_URL}"
echo "    Mock:     ${MOCK_MODE}"
echo

if ! command_exists npm; then
  echo "[x] npm was not found. Please install Node.js/npm or add npm to PATH." >&2
  exit 1
fi

if [[ "$MOCK_MODE" -eq 0 ]] && ! command_exists uv; then
  echo "[x] uv was not found. Please install uv or add uv to PATH." >&2
  exit 1
fi

PIDS=()
cleanup() {
  if ((${#PIDS[@]} > 0)); then
    echo
    echo "==> Stopping services..."
    kill "${PIDS[@]}" >/dev/null 2>&1 || true
  fi
}
trap cleanup INT TERM EXIT

BACKEND_BUSY=0
FRONTEND_BUSY=0

if [[ "$MOCK_MODE" -eq 0 ]] && is_port_open "$BACKEND_PORT"; then
  BACKEND_BUSY=1
  echo "[!] Backend port ${BACKEND_PORT} is already in use. Reusing existing backend."
fi

if is_port_open "$FRONTEND_PORT"; then
  FRONTEND_BUSY=1
  echo "[!] Frontend port ${FRONTEND_PORT} is already in use. Reusing existing frontend."
fi

if [[ "$MOCK_MODE" -eq 0 && "$BACKEND_BUSY" -eq 0 ]]; then
  if command_exists docker; then
    echo "==> Starting Postgres via docker compose..."
    if docker compose up -d db >/dev/null 2>&1; then
      echo "[+] Postgres container requested. Probing 127.0.0.1:5432 ..."
      PG_READY=0
      for _ in {1..30}; do
        if is_port_open 5432; then
          PG_READY=1
          break
        fi
        sleep 1
      done
      if [[ "$PG_READY" -eq 1 ]]; then
        echo "[+] Postgres is reachable on 127.0.0.1:5432."
      else
        echo "[!] Postgres did not respond in 30s. Check 'docker compose ps' / 'docker compose logs db'."
      fi
    else
      echo "[!] docker compose up -d db failed. Backend will retry on first request."
    fi
  else
    echo "[!] docker not on PATH. Assuming an external Postgres is reachable via DATABASE_URL."
  fi

  echo "==> Starting backend..."
  (
    uv sync
    uv run uvicorn backend.app:app --host 127.0.0.1 --port "$BACKEND_PORT"
  ) &
  PIDS+=("$!")
fi

if [[ "$FRONTEND_BUSY" -eq 0 ]]; then
  echo "==> Starting frontend..."
  (
    cd "$ROOT_DIR/frontend"
    if [[ "$MOCK_MODE" -eq 1 ]]; then
      export VITE_USE_MOCK=true
    fi
    if [[ ! -d node_modules ]]; then
      npm install
    fi
    npm run dev
  ) &
  PIDS+=("$!")
fi

if [[ "$MOCK_MODE" -eq 0 ]]; then
  echo "==> Waiting for backend health: ${HEALTH_URL}"
  if wait_for_url "$HEALTH_URL" 30; then
    echo "[+] Backend is ready."
  else
    echo "[!] Backend was not ready in 30 seconds. Check the backend output above."
  fi
fi

echo "==> Opening browser: ${FRONTEND_URL}"
open_browser "$FRONTEND_URL"

if ((${#PIDS[@]} == 0)); then
  echo
  echo "No new service was started."
  exit 0
fi

echo
echo "Services are running. Press Ctrl+C to stop."
wait "${PIDS[@]}"

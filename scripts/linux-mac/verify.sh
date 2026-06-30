#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

SKIP_SYNC=0
for arg in "$@"; do
  case "$arg" in
    --skip-sync|-s)
      SKIP_SYNC=1
      ;;
    -h|--help)
      cat <<'EOF'
Usage: ./verify.sh [--skip-sync]

Runs backend unit tests, MarkItDown availability check,
frontend helper tests, and frontend production build.
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

run_step() {
  local name="$1"
  shift
  echo
  echo "==> ${name}"
  "$@"
}

if ! command_exists uv; then
  echo "[x] uv was not found. Please install uv or add uv to PATH." >&2
  exit 1
fi

if ! command_exists npm; then
  echo "[x] npm was not found. Please install Node.js/npm or add npm to PATH." >&2
  exit 1
fi

if [[ "$SKIP_SYNC" -eq 0 ]]; then
  run_step "Sync Python dependencies" uv sync
fi

run_step "Run backend unit tests" uv run python -m unittest discover

run_step "Check MarkItDown availability" \
  uv run python -c "from kb_eval.markitdown_converter import markitdown_available; raise SystemExit(0 if markitdown_available() else 1)"

if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
  run_step "Install frontend dependencies" npm --prefix frontend install
fi

run_step "Run frontend helper tests" npm --prefix frontend run test:helpers
run_step "Build frontend" npm --prefix frontend run build

echo
echo "Verification completed."

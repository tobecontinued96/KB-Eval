#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WINDOWS_SCRIPT_DIR="${ROOT_DIR}/scripts/windows"
cd "$ROOT_DIR"

TAG="$(date +%Y%m%d-%H%M%S)"
OUTPUT_ROOT="offline-packages"
BACKEND_IMAGE=""
FRONTEND_IMAGE=""
POSTGRES_IMAGE=""
SKIP_PULL=0
INCLUDE_RUNTIME_DATA=0
NO_ARCHIVE=0

usage() {
  cat <<'EOF'
Usage: ./build-offline-package.sh [options]

Options:
  --tag VALUE              Image/package tag. Default: current timestamp.
  --output-root DIR        Output directory. Default: offline-packages.
  --backend-image IMAGE    Backend image tag to build/save.
  --frontend-image IMAGE   Frontend image tag to build/save.
  --postgres-image IMAGE   Postgres image tag to pull/save.
  --skip-pull              Use local Postgres image without pulling.
  --include-runtime-data   Include reports/logs/generated_sources in package.
  --no-archive             Leave package as a directory instead of .tar.gz.
  -h, --help               Show this help.
EOF
}

while (($#)); do
  case "$1" in
    --tag) TAG="$2"; shift 2 ;;
    --output-root) OUTPUT_ROOT="$2"; shift 2 ;;
    --backend-image) BACKEND_IMAGE="$2"; shift 2 ;;
    --frontend-image) FRONTEND_IMAGE="$2"; shift 2 ;;
    --postgres-image) POSTGRES_IMAGE="$2"; shift 2 ;;
    --skip-pull) SKIP_PULL=1; shift ;;
    --include-runtime-data) INCLUDE_RUNTIME_DATA=1; shift ;;
    --no-archive) NO_ARCHIVE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[x] Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

read_env_value() {
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

copy_dir_or_create() {
  local name="$1"
  local destination_root="$2"
  if [[ -d "$name" ]]; then
    cp -a "$name" "$destination_root/"
  else
    mkdir -p "$destination_root/$name"
  fi
}

if ! command_exists docker; then
  echo "[x] docker was not found. Install Docker or add it to PATH." >&2
  exit 1
fi

BACKEND_IMAGE="${BACKEND_IMAGE:-$(read_env_value BACKEND_IMAGE "dify-kb-eval-backend:${TAG}")}"
FRONTEND_IMAGE="${FRONTEND_IMAGE:-$(read_env_value FRONTEND_IMAGE "dify-kb-eval-frontend:${TAG}")}"
POSTGRES_IMAGE="${POSTGRES_IMAGE:-$(read_env_value POSTGRES_IMAGE "m.daocloud.io/docker.io/library/postgres:16")}"

PYTHON_IMAGE="$(read_env_value PYTHON_IMAGE "m.daocloud.io/docker.io/library/python:3.12-slim")"
NODE_IMAGE="$(read_env_value NODE_IMAGE "m.daocloud.io/docker.io/library/node:22-alpine")"
NGINX_IMAGE="$(read_env_value NGINX_IMAGE "m.daocloud.io/docker.io/library/nginx:1.27-alpine")"
APT_MIRROR="$(read_env_value APT_MIRROR "http://mirrors.aliyun.com/debian")"
APT_SECURITY_MIRROR="$(read_env_value APT_SECURITY_MIRROR "http://mirrors.aliyun.com/debian-security")"
PYPI_INDEX_URL="$(read_env_value PYPI_INDEX_URL "https://mirrors.aliyun.com/pypi/simple/")"
NPM_REGISTRY="$(read_env_value NPM_REGISTRY "https://registry.npmmirror.com")"

POSTGRES_DB="$(read_env_value POSTGRES_DB "dify_kb_eval")"
POSTGRES_USER="$(read_env_value POSTGRES_USER "dify_kb_eval")"
POSTGRES_PASSWORD="$(read_env_value POSTGRES_PASSWORD "dify_kb_eval")"
FRONTEND_PORT="$(read_env_value FRONTEND_PORT "5598")"
BACKEND_PORT="$(read_env_value BACKEND_PORT "8200")"
NETWORK_NAME="$(read_env_value DOCKER_NETWORK_NAME "dify-kb-eval-net")"
RUN_DB_BOOTSTRAP="$(read_env_value RUN_DB_BOOTSTRAP "true")"
RUN_DB_MIGRATIONS="$(read_env_value RUN_DB_MIGRATIONS "true")"
RUN_DB_INIT_ON_EMPTY="$(read_env_value RUN_DB_INIT_ON_EMPTY "true")"
RUN_DB_STAMP_HEAD_ON_INIT="$(read_env_value RUN_DB_STAMP_HEAD_ON_INIT "true")"

echo "==> Build backend image ${BACKEND_IMAGE}"
docker build \
  -t "$BACKEND_IMAGE" \
  --build-arg "PYTHON_IMAGE=$PYTHON_IMAGE" \
  --build-arg "APT_MIRROR=$APT_MIRROR" \
  --build-arg "APT_SECURITY_MIRROR=$APT_SECURITY_MIRROR" \
  --build-arg "PYPI_INDEX_URL=$PYPI_INDEX_URL" \
  -f Dockerfile .

echo "==> Build frontend image ${FRONTEND_IMAGE}"
docker build \
  -t "$FRONTEND_IMAGE" \
  --build-arg "NODE_IMAGE=$NODE_IMAGE" \
  --build-arg "NGINX_IMAGE=$NGINX_IMAGE" \
  --build-arg "NPM_REGISTRY=$NPM_REGISTRY" \
  -f frontend/Dockerfile frontend

if [[ "$SKIP_PULL" -eq 1 ]]; then
  echo "==> Check local Postgres image ${POSTGRES_IMAGE}"
  docker image inspect "$POSTGRES_IMAGE" >/dev/null
else
  echo "==> Pull Postgres image ${POSTGRES_IMAGE}"
  docker pull "$POSTGRES_IMAGE"
fi

PACKAGE_NAME="dify-kb-eval-offline-${TAG}"
PACKAGE_DIR="${OUTPUT_ROOT}/${PACKAGE_NAME}"
IMAGE_DIR="${PACKAGE_DIR}/images"

if [[ -e "$PACKAGE_DIR" ]]; then
  echo "[x] Package directory already exists: $PACKAGE_DIR" >&2
  exit 1
fi

mkdir -p "$IMAGE_DIR"

echo "==> Save images"
docker save -o "${IMAGE_DIR}/backend.tar" "$BACKEND_IMAGE"
docker save -o "${IMAGE_DIR}/frontend.tar" "$FRONTEND_IMAGE"
docker save -o "${IMAGE_DIR}/postgres.tar" "$POSTGRES_IMAGE"

cp \
  docker-compose.offline.yml \
  "${WINDOWS_SCRIPT_DIR}/deploy-offline.ps1" \
  "${WINDOWS_SCRIPT_DIR}/deploy-offline.bat" \
  "${SCRIPT_DIR}/deploy-offline.sh" \
  "$PACKAGE_DIR/"
copy_dir_or_create datasets "$PACKAGE_DIR"
copy_dir_or_create docs "$PACKAGE_DIR"
copy_dir_or_create config "$PACKAGE_DIR"

for dir in reports logs generated_sources; do
  if [[ "$INCLUDE_RUNTIME_DATA" -eq 1 ]]; then
    copy_dir_or_create "$dir" "$PACKAGE_DIR"
  else
    mkdir -p "$PACKAGE_DIR/$dir"
  fi
done

cat > "${PACKAGE_DIR}/.env.offline" <<EOF
POSTGRES_DB=${POSTGRES_DB}
POSTGRES_USER=${POSTGRES_USER}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_IMAGE=${POSTGRES_IMAGE}

BACKEND_IMAGE=${BACKEND_IMAGE}
FRONTEND_IMAGE=${FRONTEND_IMAGE}
BACKEND_PORT=${BACKEND_PORT}
FRONTEND_PORT=${FRONTEND_PORT}
DOCKER_NETWORK_NAME=${NETWORK_NAME}

DOCKER_DATABASE_URL=
RUN_DB_BOOTSTRAP=${RUN_DB_BOOTSTRAP}
RUN_DB_MIGRATIONS=${RUN_DB_MIGRATIONS}
RUN_DB_INIT_ON_EMPTY=${RUN_DB_INIT_ON_EMPTY}
RUN_DB_STAMP_HEAD_ON_INIT=${RUN_DB_STAMP_HEAD_ON_INIT}
DB_WAIT_TIMEOUT_SECONDS=60

LOG_LEVEL=INFO
LOG_DIR=logs
LOG_TO_FILE=true
EVAL_RUNNER_CONCURRENCY=8
EVAL_RUNNER_TICK_MS=500
EVAL_RUNNER_SUBPROCESS=enabled
MINERU_API_TOKEN=
MARKITDOWN_COMMAND=
EOF

cat > "${PACKAGE_DIR}/OFFLINE-MANIFEST.txt" <<EOF
Dify-KB-Eval offline package
created_at=$(date '+%Y-%m-%d %H:%M:%S')
backend_image=${BACKEND_IMAGE}
frontend_image=${FRONTEND_IMAGE}
postgres_image=${POSTGRES_IMAGE}
frontend_url=http://127.0.0.1:${FRONTEND_PORT}

Deploy on the offline machine:
  Windows: .\\deploy-offline.ps1
  Linux/macOS: bash ./deploy-offline.sh
EOF

if [[ "$NO_ARCHIVE" -eq 0 ]]; then
  archive_path="${PACKAGE_DIR}.tar.gz"
  echo "==> Compress offline package"
  tar -czf "$archive_path" -C "$OUTPUT_ROOT" "$PACKAGE_NAME"
  echo "[+] Offline package: $archive_path"
else
  echo "[+] Offline package directory: $PACKAGE_DIR"
fi

cat <<EOF

Copy the package to the offline machine, extract it, then run:
  bash ./deploy-offline.sh
EOF

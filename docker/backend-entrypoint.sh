#!/usr/bin/env sh
set -eu

DATABASE_URL_WAS_SET=0
if [ -n "${DATABASE_URL:-}" ]; then
  DATABASE_URL_WAS_SET=1
fi

: "${POSTGRES_HOST:=db}"
: "${POSTGRES_PORT:=5432}"
: "${POSTGRES_DB:=dify_kb_eval}"
: "${POSTGRES_USER:=dify_kb_eval}"
: "${POSTGRES_PASSWORD:=dify_kb_eval}"
: "${RUN_DB_BOOTSTRAP:=true}"
: "${RUN_DB_MIGRATIONS:=false}"
: "${RUN_DB_INIT_ON_EMPTY:=true}"
: "${RUN_DB_STAMP_HEAD_ON_INIT:=true}"
: "${DB_WAIT_TIMEOUT_SECONDS:=60}"

if [ -z "${DATABASE_URL:-}" ]; then
  DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
fi

export DATABASE_URL
export DATABASE_URL_WAS_SET
export RUN_DB_BOOTSTRAP
export RUN_DB_INIT_ON_EMPTY
export RUN_DB_STAMP_HEAD_ON_INIT

python - <<'PY'
import os
import socket
import sys
import time
from urllib.parse import urlparse

url = os.environ.get("DATABASE_URL", "")
parsed_url = (
    url.replace("postgresql+asyncpg://", "postgresql://", 1)
       .replace("postgresql+psycopg://", "postgresql://", 1)
)
parsed = urlparse(parsed_url)

if os.environ.get("DATABASE_URL_WAS_SET") == "1":
    host = parsed.hostname or os.environ.get("POSTGRES_HOST", "db")
    port = parsed.port or int(os.environ.get("POSTGRES_PORT", "5432"))
else:
    host = os.environ.get("POSTGRES_HOST", "db")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))

timeout = int(os.environ.get("DB_WAIT_TIMEOUT_SECONDS", "60"))
deadline = time.time() + timeout

print(f"[entrypoint] waiting for database at {host}:{port} ({timeout}s timeout)", flush=True)
while True:
    try:
        with socket.create_connection((host, port), timeout=3):
            print("[entrypoint] database TCP port is reachable", flush=True)
            break
    except OSError as exc:
        if time.time() >= deadline:
            print(f"[entrypoint] database did not become reachable: {exc}", file=sys.stderr, flush=True)
            sys.exit(1)
        time.sleep(1)
PY

python - <<'PY'
import os
import sys
from pathlib import Path

from sqlalchemy import inspect

from backend.db.session import get_engine, init_db


def truthy(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).lower() in {"1", "true", "yes", "on"}


engine = get_engine()
try:
    has_runs_table = inspect(engine).has_table("runs")
except Exception as exc:
    print(
        f"[entrypoint] failed to inspect database before startup: {type(exc).__name__}: {exc}",
        file=sys.stderr,
        flush=True,
    )
    raise

if has_runs_table:
    print("[entrypoint] existing database schema detected", flush=True)
elif truthy("RUN_DB_INIT_ON_EMPTY", "true"):
    print("[entrypoint] empty database detected; creating application tables", flush=True)
    previous_bootstrap = os.environ.get("RUN_DB_BOOTSTRAP")
    os.environ["RUN_DB_BOOTSTRAP"] = "true"
    init_db()
    if previous_bootstrap is None:
        os.environ.pop("RUN_DB_BOOTSTRAP", None)
    else:
        os.environ["RUN_DB_BOOTSTRAP"] = previous_bootstrap

    if truthy("RUN_DB_STAMP_HEAD_ON_INIT", "true"):
        from alembic import command
        from alembic.config import Config

        root = Path.cwd()
        cfg = Config(str(root / "alembic.ini"))
        cfg.set_main_option("script_location", str(root / "backend" / "alembic"))
        print("[entrypoint] stamping alembic version to head for fresh schema", flush=True)
        command.stamp(cfg, "head")
else:
    print(
        "[entrypoint] database is empty and RUN_DB_INIT_ON_EMPTY=false; "
        "startup will rely on the application/migration policy",
        flush=True,
    )
PY

case "$(printf '%s' "$RUN_DB_MIGRATIONS" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on)
    echo "[entrypoint] running alembic upgrade head"
    python -m alembic upgrade head
    ;;
esac

exec "$@"

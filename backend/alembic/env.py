"""Alembic environment.

Why this looks the way it does
------------------------------
* We reuse ``backend.db.session``'s ``$DATABASE_URL`` resolution chain
  (env var -> ``.env`` in the repo root) so the migration tool can
  never point at a different DB than the FastAPI app does. Without
  this, ``alembic upgrade head`` and ``uvicorn`` could disagree and
  you'd stamp the wrong DB.
* ``target_metadata = backend.db.base.Base.metadata`` after importing
  ``backend.db.models`` — the import side-effect registers every ORM
  table on ``Base.metadata`` so ``--autogenerate`` can diff against it.
* We pin the SQLAlchemy URL on the Alembic config object so the
  autogenerate / online / offline modes all see the same value, even
  though we hand-roll an ``Engine`` for the online mode.
* We deliberately do **not** call ``backend.db.session.get_engine()``
  in the offline path — offline mode (used by ``alembic upgrade --sql``)
  must not open a real connection, and ``get_engine()`` would try.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make ``backend.*`` importable when ``env.py`` is invoked via
# ``alembic ...`` from any CWD. ``alembic.ini``'s ``prepend_sys_path = .``
# already does this when run from the repo root, but the sys.path tweak
# below keeps things working for IDEs / scripts that launch alembic
# from elsewhere.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Reuse the project's URL resolution so the URL the migrations run
# against is always the one the backend would pick up.
from backend.db.session import _normalize_url, _try_load_dotenv  # noqa: E402

from backend.db.base import Base  # noqa: E402

# Importing the models module registers the tables on ``Base.metadata``
# so ``--autogenerate`` can compare against them. Mirrors the import in
# ``backend/db/session.py``.
import backend.db.models  # noqa: E402,F401

config = context.config

# Honour ``fileConfig`` if a logging section exists in ``alembic.ini``.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_database_url() -> str:
    """Mirror ``backend.db.session.get_engine``'s URL resolution.

    Returns the same string the running backend would use, with
    asyncpg / postgresql:// forms normalised to ``postgresql+psycopg://``
    so SQLAlchemy 2.x's sync driver is used (matches the rest of the
    backend, which is sync on the request path).
    """
    _try_load_dotenv()
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        # Don't silently fall back to sqlite here — alembic should
        # never be pointed at a different DB than the one the app uses.
        raise RuntimeError(
            "DATABASE_URL is not set; alembic refuses to guess. "
            "Set it in the environment or in the repo-root .env."
        )
    return _normalize_url(url)


target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout (used by ``alembic upgrade --sql``)."""

    url = _resolve_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Don't emit ``IF NOT EXISTS`` rewrites; the SQL we emit should
        # match what the online mode would run against an empty DB.
        render_as_batch=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations to a live database."""

    config.set_main_option("sqlalchemy.url", _resolve_database_url())
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Don't require alembic to manage type comparison beyond
            # what SQLAlchemy already does — keeps autogenerate diffs
            # honest.
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

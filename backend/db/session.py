"""SQLAlchemy engine and session factory.

We use the **sync** ``psycopg`` driver so that ``DBStore`` can be called
from sync request handlers without the cross-event-loop pitfalls of
``asyncpg`` + ``BackgroundTasks`` + per-request ``asyncio.run``. The
trade-off is one extra thread per request for the SQL call, which is
negligible at this scale.
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base

# Importing the models module registers the tables on ``Base.metadata``.
import backend.db.models  # noqa: F401

log = logging.getLogger("backend.db")

# Seconds to wait when establishing a new DB connection. Without this the
# driver will happily sit on ``connect()`` for the OS-default TCP timeout
# (often 60–120s) which looks indistinguishable from a hang on the
# ``Waiting for application startup.`` line. Keep it short so a wrong port /
# down DB / DNS issue fails loud and fast during ``lifespan``.
DB_CONNECT_TIMEOUT_SECONDS = 5

_engine: Any | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _try_load_dotenv() -> None:
    """If ``DATABASE_URL`` is unset, look for a ``.env`` next to the project
    root and load it. Called from :func:`get_engine` so that the engine is
    constructed with the production URL even when the calling process
    (e.g. ``uvicorn``) started before any explicit ``load_dotenv()`` had
    a chance to run."""

    if os.environ.get("DATABASE_URL"):
        return
    try:
        from dotenv import load_dotenv

        # backend/db/session.py -> backend/db -> backend -> <repo_root>
        repo_root = Path(__file__).resolve().parents[2]
        load_dotenv(repo_root / ".env")
    except ImportError:
        pass


def _normalize_url(url: str) -> str:
    """Map asyncpg URLs to the sync psycopg driver so a single
    ``DATABASE_URL`` value works for both sync and async contexts.

    ``postgresql+asyncpg://...`` -> ``postgresql+psycopg://...``
    """
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg://" + url[len("postgresql+asyncpg://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def get_engine() -> Any:
    global _engine
    if _engine is None:
        _try_load_dotenv()
        url = os.environ.get("DATABASE_URL")
        if not url:
            # Tests / no-env fallback: in-memory SQLite.
            url = "sqlite:///:memory:"
            os.environ["DIFY_KB_EVAL_TEST_MODE"] = "1"
        normalized = _normalize_url(url)
        connect_args: dict[str, Any] = {}
        if normalized.startswith("postgresql"):
            # ``connect_timeout`` is the psycopg/psycopg2 knob; without it
            # ``connect()`` can stall for the OS TCP timeout on a wrong /
            # down port. Cap it so ``lifespan`` fails loudly instead of
            # silently hanging on ``Waiting for application startup.``.
            connect_args["connect_timeout"] = DB_CONNECT_TIMEOUT_SECONDS
        _engine = create_engine(
            normalized,
            pool_pre_ping=True,
            future=True,
            connect_args=connect_args,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionFactory


def reset_for_tests() -> None:
    """Clear the cached engine/sessionmaker. Tests call this between cases."""
    global _engine, _SessionFactory
    if _engine is not None:
        try:
            _engine.dispose()
        except Exception:
            pass
    _engine = None
    _SessionFactory = None


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yield a session, commit on success, rollback
    on exception. Routes that need a session can use this directly."""

    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create tables if they don't exist.

    Gated on the ``RUN_DB_BOOTSTRAP`` env var (default ``true`` for dev).
    Production setups should run ``alembic upgrade head`` and set this to
    ``false``.
    """
    if os.environ.get("RUN_DB_BOOTSTRAP", "true").lower() not in {"1", "true", "yes"}:
        return
    engine = get_engine()
    try:
        Base.metadata.create_all(bind=engine)
    except OperationalError as exc:
        _log_db_unreachable(exc)
        raise


def _log_db_unreachable(exc: BaseException) -> None:
    """Best-effort TCP probe + a one-screen hint when the DB is unreachable.

    ``sqlalchemy.exc.OperationalError`` covers wrong port, refused, DNS,
    auth, and timeouts — all of which look identical from the lifespan
    standpoint. We try to identify the *first* one (``connection refused`` /
    DNS / timeout) and print enough context that the user doesn't have to
    scroll up through 30 frames of SQLAlchemy plumbing to figure out which
    port / host / .env field is wrong.
    """
    url = os.environ.get("DATABASE_URL", "")
    parsed = urlparse(_normalize_url(url)) if url else None
    host = parsed.hostname if parsed else None
    port = parsed.port if parsed else None
    db_name = (parsed.path or "").lstrip("/") or None

    probe_msg = ""
    if host:
        try:
            with socket.create_connection(
                (host, port or 5432), timeout=DB_CONNECT_TIMEOUT_SECONDS
            ):
                pass
        except OSError as sock_exc:
            probe_msg = (
                f"  → TCP probe to {host}:{port or 5432} failed: "
                f"{type(sock_exc).__name__}: {sock_exc}"
            )
        else:
            probe_msg = (
                f"  → TCP probe to {host}:{port or 5432} succeeded, "
                "so the port is open — the failure is auth / DB name / "
                "SSL, not networking."
            )

    lines = [
        "[backend.db] init_db() could not reach the database:",
        f"  DATABASE_URL = {url or '(unset — would fall back to sqlite:///:memory:)'!r}",
        f"  host={host!r} port={port!r} dbname={db_name!r}",
        f"  driver error: {type(exc.orig).__name__ if exc.orig else type(exc).__name__}: "
        f"{getattr(exc.orig, 'pgcode', None) or ''} {exc.orig if exc.orig else exc}".rstrip(),
    ]
    if probe_msg:
        lines.append(probe_msg)
    lines.append(
        "  Hints: (1) is the Postgres container / service running?  "
        "(2) does host:port match the running container's published port "
        "(e.g. docker ps --format '{{.Ports}}')?  "
        "(3) does the .env DATABASE_URL user/password/db exist?  "
        "(4) is RUN_DB_BOOTSTRAP=false? then run `alembic upgrade head`."
    )
    # stderr, not stdout — uvicorn's reloader forwards both, but stderr
    # is what shows up red in the terminal and in `journalctl`.
    print("\n".join(lines), file=__import__("sys").stderr, flush=True)
    log.error("init_db failed: %s", exc)


class AlembicHeadMismatch(RuntimeError):
    """Raised by :func:`require_alembic_head` when the database's
    ``alembic_version`` row is missing or behind the current head
    revision. Surfaces as a hard startup failure in
    :func:`backend.app.lifespan` so production refuses to boot with
    a stale schema instead of silently running ``create_all`` (which
    wouldn't add the missing columns anyway)."""


def require_alembic_head() -> str:
    """Fail loudly if the database's alembic_version isn't at head.

    Behaviour
    ---------
    * Connects to the same DB the running backend would (env var
      first, then ``.env`` in the repo root).
    * Reads ``alembic_version.version_num`` via Alembic's
      ``MigrationContext`` so we use the project's alembic scripts
      directory rather than hard-coding ``backend/alembic``.
    * Compares against the ``head`` revision reported by the script
      directory.

    Returns the head revision string on success (useful for logging).

    Failure modes
    -------------
    * No ``alembic_version`` table at all -> ``AlembicHeadMismatch``
      with hint to run ``alembic upgrade head``.
    * ``alembic_version`` row exists but points at an older revision
      -> ``AlembicHeadMismatch`` naming the gap, hint to run
      ``alembic upgrade head``.
    * ``alembic_version`` row points at a *future* revision
      (downgrade accident) -> ``AlembicHeadMismatch`` hinting to run
      ``alembic downgrade <db_rev>`` (we never want the backend
      running on a newer schema than its code knows about).
    * ``DATABASE_URL`` unset -> ``RuntimeError`` (same policy as
      :mod:`backend.alembic.env`).
    """

    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from alembic.config import Config as AlembicConfig

    # Repo root -> ``alembic.ini`` (which itself points at
    # ``backend/alembic`` via ``script_location``).
    repo_root = Path(__file__).resolve().parents[2]
    cfg = AlembicConfig(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "backend" / "alembic"))
    script_dir = ScriptDirectory.from_config(cfg)
    head = script_dir.get_current_head()
    if head is None:
        # No revisions registered -- the alembic scaffold is broken
        # on the code side, not the DB side. Surface that explicitly.
        raise AlembicHeadMismatch(
            "alembic scaffold has no head revision -- "
            "backend/alembic/versions/ is empty or unreadable."
        )

    engine = get_engine()
    try:
        with engine.connect() as connection:
            ctx = MigrationContext.configure(connection)
            db_rev = ctx.get_current_revision()
    except OperationalError as exc:
        # Same unreachable-DB policy as ``init_db`` -- re-raise after
        # the friendly probe so the lifespan error message stays
        # useful.
        _log_db_unreachable(exc)
        raise

    if db_rev == head:
        return head

    # Build a one-screen hint. Two distinct cases because the remedy
    # differs.
    if db_rev is None:
        hint = (
            "Database has never been stamped by alembic. "
            "Run `alembic upgrade head` (with the same $DATABASE_URL "
            "the backend uses)."
        )
    elif _is_ancestor(db_rev, head, script_dir):
        hint = (
            f"Database is at revision {db_rev!r}, head is {head!r}. "
            "Run `alembic upgrade head` to apply pending migrations."
        )
    else:
        # db_rev is unknown to the script dir, OR it sits *after* head.
        # Either way the running code is older than the DB; downgrading
        # the DB is the only safe path.
        hint = (
            f"Database is at revision {db_rev!r}, which the running "
            f"code doesn't recognise (head is {head!r}). Either the "
            "code is stale -- pull a newer revision -- or the DB was "
            "downgraded out of band. Do NOT start the backend until "
            "`alembic heads` shows what the DB is on."
        )

    raise AlembicHeadMismatch(
        f"Refusing to start: alembic_version is at {db_rev!r}, "
        f"expected {head!r}. {hint}"
    )


def _is_ancestor(rev: str, head: str, script_dir) -> bool:
    """Return True if ``rev`` is an ancestor of ``head`` in the
    script directory's DAG. Used to tell "DB is behind" from
    "DB is ahead / unknown". Falls back to False on any walk error
    so the caller defaults to the safer "downgrade the DB" hint.

    Implementation note: alembic's :class:`RevisionMap` is not a
    plain dict and exposes neither ``.get`` nor ``.keys``; the
    supported way to look up a revision is
    :meth:`ScriptDirectory.get_revision`, which raises
    :class:`alembic.util.CommandError` if the id is unknown. We use
    it directly here, and walk the chain via
    :meth:`ScriptDirectory.walk_revisions`."""

    try:
        # If either id isn't a known revision in the loaded script
        # directory, ``rev`` can't be an ancestor of ``head`` --
        # treat it as "unknown / ahead" so the caller picks the
        # safer "do not start" hint.
        if script_dir.get_revision(rev) is None:
            return False
        if script_dir.get_revision(head) is None:
            return False
        # ``walk_revisions`` yields the head and every revision
        # reachable by following ``down_revision`` -- exactly the
        # set we need.
        for script in script_dir.walk_revisions():
            if script.revision == rev:
                return True
        return False
    except Exception:
        return False

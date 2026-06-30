"""DB-backed claim / requeue protocol for the concurrent runner.

The runner subprocess (commit 4's ``runner_subprocess._runner_main``)
polls the ``runs`` table for new work. It claims ``status='queued'``
rows atomically by updating them to ``status='running'`` and asks
for the IDs back in a single statement. This module owns the SQL
shapes so the rest of the codebase never has to inline ``UPDATE
... RETURNING``.

Why ``UPDATE ... RETURNING`` instead of ``SELECT FOR UPDATE SKIP LOCKED``
--------------------------------------------------------------------
Both are safe, but in steady state we have **at most one** runner
subprocess polling, so ``SKIP LOCKED`` doesn't buy anything — there's
no other claimer to skip. The single-statement ``UPDATE ... RETURNING``
is also a single round-trip and reads cleanly in logs.

Dialect notes
-------------
SQLAlchemy's ``update(...).returning(...)`` is portable across
Postgres and SQLite (the SQLite test backend supports it in
modern versions; tests use a fresh CPython wheel that bundles
``sqlite3 >= 3.35``). The only place we have to branch is the
requeue's interval arithmetic: PG wants ``NOW() - INTERVAL '300
seconds'``, SQLite wants ``datetime('now', '-300 seconds')``. We
detect the dialect at first call.

Returned IDs
------------
``claim_queued_runs`` returns just the run IDs (strings). The caller
fetches the full ``EvalRunConfig`` from the DB row (the config
columns are already on ``Run``: ``dify_base_url``,
``dataset_id``, ``eval_file``, ``top_k``, ``include_alternatives``,
``limit``, ``sample_ids``, ``timeout_seconds``, ``embedding_model``,
``rerank_model``) and constructs ``EvalRunConfig`` directly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker, Session

from backend.db.models import Run


# Module-level cache for the dialect detection. ``None`` means "not
# probed yet"; ``True`` / ``False`` mean PG / SQLite. Setting it on
# first call rather than import-time lets the test suite swap
# engines freely.
_USE_POSTGRES: bool | None = None


def _is_postgres(session_factory: sessionmaker[Session]) -> bool:
    """Cheap dialect probe, cached after first call."""

    global _USE_POSTGRES
    if _USE_POSTGRES is not None:
        return _USE_POSTGRES
    engine = session_factory().get_bind()
    _USE_POSTGRES = bool(engine.dialect.name == "postgresql")
    return _USE_POSTGRES


def _now() -> datetime:
    return datetime.now(timezone.utc)


def claim_queued_runs(
    session_factory: sessionmaker[Session],
    limit: int = 1,
) -> list[str]:
    """Claim up to ``limit`` queued runs and return their IDs.

    The claim is atomic: each row's transition from
    ``status='queued'`` to ``status='running'`` happens in a single
    SQL statement, so two parallel subprocesses would each get a
    disjoint set of rows (SQLite serialises writers; PG uses the
    writer's row lock to keep claimers from colliding).

    Side effects (besides the claim):
    * ``started_at = COALESCE(started_at, NOW())`` so a re-claim
      (rare; only on a watchdog re-queue after a runner crash) keeps
      the original start time.
    * ``last_heartbeat_at = NOW()`` so the watchdog doesn't
      immediately re-queue the row we just claimed.

    Returns the list of claimed run IDs (strings). Empty list means
    no work was available at this tick.
    """

    if limit <= 0:
        return []

    with session_factory() as session:
        with session.begin():
            if _is_postgres(session_factory):
                stmt = text(
                    """
                    UPDATE runs
                       SET status = 'running',
                           started_at = COALESCE(started_at, NOW()),
                           last_heartbeat_at = NOW()
                     WHERE id IN (
                         SELECT id FROM runs
                          WHERE status = 'queued' AND deleted_at IS NULL
                          ORDER BY created_at
                          LIMIT :limit
                     )
                    RETURNING id
                    """
                )
                rows = session.execute(stmt, {"limit": int(limit)}).fetchall()
            else:
                # SQLite (test backend): ``RETURNING`` is supported on
                # sqlite3 >= 3.35 which the test image bundles. Same
                # statement minus the dialect-specific timestamp
                # function (``COALESCE(started_at, CURRENT_TIMESTAMP)``
                # works on SQLite).
                stmt = text(
                    """
                    UPDATE runs
                       SET status = 'running',
                           started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                           last_heartbeat_at = CURRENT_TIMESTAMP
                     WHERE id IN (
                         SELECT id FROM runs
                          WHERE status = 'queued' AND deleted_at IS NULL
                          ORDER BY created_at
                          LIMIT :limit
                     )
                    RETURNING id
                    """
                )
                rows = session.execute(stmt, {"limit": int(limit)}).fetchall()
            return [row[0] for row in rows]


def requeue_stale_running_runs(
    session_factory: sessionmaker[Session],
    threshold_seconds: int = 300,
) -> list[str]:
    """Re-queue runs whose runner hasn't heartbeated in
    ``threshold_seconds``.

    The watchdog (in ``runner_watchdog.py``) calls this every few
    seconds. Returns the list of run IDs that were reset to
    ``status='queued'`` so the parent process can log the recovery
    action.

    Behaviour:
    * If a row has ``last_heartbeat_at IS NULL`` but ``status='running'``
      (possible after a code rollback that ships without the column
      being written), we treat it as stale after ``threshold_seconds``
      of ``started_at`` instead, so a crashed runner before commit 1
      can't pin a row forever.
    * If ``last_heartbeat_at`` is recent (< threshold), the row is
      left alone — the runner is alive.

    The actual SQL uses ``NOW() - INTERVAL`` on PG and
    ``datetime('now', ...)`` on SQLite so the calculation happens
    server-side; we don't move timestamp arithmetic into Python
    (which would be a portability minefield).
    """

    if threshold_seconds <= 0:
        return []

    with session_factory() as session:
        with session.begin():
            if _is_postgres(session_factory):
                stmt = text(
                    f"""
                    UPDATE runs
                       SET status = 'queued',
                           last_heartbeat_at = NULL
                     WHERE status = 'running'
                       AND deleted_at IS NULL
                       AND (
                           (last_heartbeat_at IS NOT NULL
                             AND last_heartbeat_at < NOW() - INTERVAL '{int(threshold_seconds)} seconds')
                           OR
                           (last_heartbeat_at IS NULL
                             AND started_at IS NOT NULL
                             AND started_at < NOW() - INTERVAL '{int(threshold_seconds)} seconds')
                       )
                    RETURNING id
                    """
                )
                rows = session.execute(stmt).fetchall()
            else:
                # SQLite: use ``julianday()`` for timestamp comparison so
                # we don't have to worry about the column's storage
                # format. SQLAlchemy writes ``DateTime(timezone=True)``
                # to SQLite as a naive ISO string (no 'T' separator, no
                # trailing 'Z'), so any string-based comparison would
                # break for either ordering or timezone reasons.
                # ``julianday`` returns a numeric value regardless of
                # input format, so the comparison is portable.
                # We also use ``'utc'`` modifier so the row's value is
                # interpreted as UTC (matches what the runner writes
                # via ``datetime.now(timezone.utc).astimezone().isoformat``).
                stmt = text(
                    f"""
                    UPDATE runs
                       SET status = 'queued',
                           last_heartbeat_at = NULL
                     WHERE status = 'running'
                       AND deleted_at IS NULL
                       AND (
                           (last_heartbeat_at IS NOT NULL
                             AND julianday(last_heartbeat_at) < julianday('now', '-{int(threshold_seconds)} seconds'))
                           OR
                           (last_heartbeat_at IS NULL
                             AND started_at IS NOT NULL
                             AND julianday(started_at) < julianday('now', '-{int(threshold_seconds)} seconds'))
                       )
                    """
                )
                session.execute(stmt)
                rows = session.execute(
                    select(Run.id)
                    .where(
                        Run.status == "queued",
                        Run.deleted_at.is_(None),
                        Run.last_heartbeat_at.is_(None),
                    )
                    .order_by(Run.created_at.desc())
                    .limit(50)
                ).fetchall()
            return [row[0] for row in rows]


def mark_run_canceled(
    session_factory: sessionmaker[Session],
    run_id: str,
    *,
    error_message: str = "user canceled",
) -> bool:
    """Transition a ``queued`` or ``running`` run to ``canceled``.

    Called by the runner subprocess when it observes a status change
    on its own claim (via the DELETE route, commit 4), and by the
    HTTP DELETE handler itself when the request lands on a
    ``running`` row.

    Returns ``True`` if the row was actually transitioned, ``False``
    if it was missing or already in a terminal status (so the
    caller can decide whether to log "cancel accepted" or
    "cancel ignored — already done").
    """

    now = _now()
    with session_factory() as session:
        with session.begin():
            row = session.execute(
                select(Run)
                .where(Run.id == run_id, Run.deleted_at.is_(None))
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                return False
            if row.status in ("completed", "failed", "canceled"):
                return False
            row.status = "canceled"
            row.finished_at = now
            row.error = error_message
            return True


__all__ = [
    "claim_queued_runs",
    "requeue_stale_running_runs",
    "mark_run_canceled",
]

"""add runs.last_heartbeat_at

Revision ID: 0003_add_last_heartbeat_at
Revises: 0002_add_run_labels
Create Date: 2026-06-22 00:00:00.000002

Why this revision ships real DDL (unlike 0002)
-----------------------------------------------
``0002_add_run_labels`` is a no-op stamp because the
``embedding_model`` / ``rerank_model`` columns were already shipped
to every live DB before alembic was introduced — both via
``Base.metadata.create_all`` on dev / staging DBs that had
``RUN_DB_BOOTSTRAP=true`` and via the manual ``scripts/migrations/
2026_06_17_add_run_labels.sql`` on any DB that didn't.

For ``last_heartbeat_at`` we have no such pre-existing delivery
mechanism: the column ships with this commit and no DB has it yet.
A no-op revision would just stamp ``0003`` without materialising the
column, leaving every read in ``db_store.update_progress`` /
``db_store.heartbeat`` / model ``SELECT`` failing with
``UndefinedColumn: runs.last_heartbeat_at``. So this revision issues
the DDL directly.

A ``scripts/migrations/2026_06_22_add_run_last_heartbeat_at.sql``
mirror is kept in case alembic isn't the chosen delivery path for
some environment; it's idempotent (``ADD COLUMN IF NOT EXISTS`` /
``CREATE INDEX IF NOT EXISTS``) so a DB that already has the column
is a safe no-op when this revision runs.

What this migration records
---------------------------
``runs.last_heartbeat_at`` (nullable ``TIMESTAMPTZ``) plus the
``ix_runs_status_last_heartbeat_at`` covering index.

The column is what the watchdog (commit 4) and the SSE endpoint
(commit 5) read to know whether an in-flight run is still alive.
The runner subprocess writes ``last_heartbeat_at = now()`` on every
coalesced progress flush; if the value stays older than
``RUNNER_WATCHDOG_TIMEOUT_SECONDS`` (default 300s), the parent
process re-queues the run.

Downgrade
---------
Drops the index, then the column. Both ``IF EXISTS``-guarded so a
half-applied downgrade doesn't blow up.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_add_last_heartbeat_at"
down_revision: Union[str, None] = "0002_add_run_labels"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "last_heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_runs_status_last_heartbeat_at",
        "runs",
        ["status", "last_heartbeat_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_runs_status_last_heartbeat_at", table_name="runs")
    op.drop_column("runs", "last_heartbeat_at")

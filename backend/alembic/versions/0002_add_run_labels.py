"""add runs.embedding_model and runs.rerank_model

Revision ID: 0002_add_run_labels
Revises: 0001_baseline
Create Date: 2026-06-22 00:00:00.000001

What this migration records
----------------------------
Adds a node to the Alembic chain that says: "any DB past this
revision is expected to have ``runs.embedding_model`` and
``runs.rerank_model`` as nullable VARCHAR(128) columns."

Why upgrade() is empty
----------------------
The columns were originally shipped by two paths that pre-date
Alembic:

1. ``backend/db/models.py`` (commit c44a513, 2026-06-15) added the
   ``mapped_column(String(128), nullable=True)`` declarations.
2. ``scripts/migrations/2026_06_17_add_run_labels.sql`` (commit
   4d0dcd8, 2026-06-17) ran ``ALTER TABLE ... ADD COLUMN IF NOT
   EXISTS`` against every DB that hadn't picked the columns up
   automatically via path (1).

Because every environment in the wild today already has the columns
-- one way or the other -- this revision cannot issue DDL. If it
tried ``op.add_column('runs', sa.Column(...))`` it would fail with
``duplicate column name: embedding_model`` on every existing DB, and
silently succeed on a fresh DB that just went through ``init_db()``
(which also calls ``Base.metadata.create_all`` and therefore
already includes the columns).

Alembic's own ``alembic_version`` row guards against re-running this
revision, so the empty upgrade is safe: it just bumps the version
row from ``0001_baseline`` to ``0002_add_run_labels``.

If a hypothetical fresh DB ever appears where ``runs`` lacks the
columns, this revision would need to grow real DDL. Until then, the
``Base.metadata.create_all`` call inside ``backend.db.session.init_db()``
remains the single source of truth for "what columns ``runs`` has",
and Alembic only records that fact.

Downgrade
---------
We downgrade by *forgetting* that we recorded 0002 as applied --
Alembic treats ``alembic_version`` as the only source of truth, so
the equivalent of undoing this empty migration is rewriting the
version row back to ``0001_baseline``. That happens automatically
because ``downgrade()`` is also a no-op and Alembic rewinds the
version row after a successful downgrade.

If you actually want to drop the columns, that is a separate,
destructive operation -- see ``scripts/migrations/2026_06_17_add_run_labels.down.sql``.
"""
from __future__ import annotations

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "0002_add_run_labels"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op by design; see module docstring. Alembic will still bump
    # ``alembic_version`` from 0001_baseline -> 0002_add_run_labels
    # after this function returns successfully.
    pass


def downgrade() -> None:
    # Symmetric no-op. Alembic rewinds ``alembic_version`` from
    # 0002_add_run_labels -> 0001_baseline automatically after this
    # function returns.
    pass

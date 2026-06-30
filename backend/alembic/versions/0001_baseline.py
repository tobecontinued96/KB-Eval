"""baseline: mark the pre-alembic schema as managed

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-22 00:00:00.000000

Why this revision is empty
--------------------------
Pre-alembic, the project's tables are created at backend startup by
``backend.db.session.init_db()`` -> ``Base.metadata.create_all(...)``.
That call has been running on every dev / staging / production
instance since the schema was first introduced, so by the time alembic
came along every database that exists in the wild already has the
``runs`` / ``run_summaries`` / ``run_reports`` tables in whatever
shape the running code expected on the day of its most recent
``create_all``.

If this revision tried to recreate those tables, ``alembic upgrade
head`` would fail with ``relation "runs" already exists`` on every
DB that already has them. So we deliberately make this revision a
no-op on the upgrade path — it exists only so the ``alembic_version``
table gains a row recording "this DB is now managed by alembic, at
revision 0001_baseline".

How to onboard an existing DB
-----------------------------
After deploying this revision, run ONCE per environment:

    alembic stamp 0001_baseline

That writes the row into ``alembic_version`` without executing any
DDL. Subsequent ``alembic upgrade head`` calls will then only apply
revisions strictly *after* 0001_baseline (i.e. the
``2026_06_17_add_run_labels`` migration, once it's been ported into
alembic as revision 0002).

How to onboard a fresh DB
-------------------------
A clean database has no tables, so 0001 alone isn't enough — you
still need ``init_db()`` to bootstrap the schema, or a future 0000
revision that explicitly creates everything. For now the supported
path is:

  1. Let the backend start once (``RUN_DB_BOOTSTRAP=true``) so
     ``create_all`` materialises the tables.
  2. ``alembic stamp 0001_baseline`` to record the state.
  3. ``alembic upgrade head`` from then on.

Once we're confident no production DB needs the create_all bootstrap
anymore, 0000_create_initial_tables will replace this onboarding
sequence — see ``docs/持久化设计.md`` § 3 for the long-term plan.
"""
from __future__ import annotations

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Intentional no-op: see module docstring. The pre-existing schema
    # was created by ``Base.metadata.create_all``; we only need to
    # register the alembic version row, which ``alembic stamp`` does
    # for us without calling ``upgrade()``.
    pass


def downgrade() -> None:
    # Downgrading past the baseline means "tell Alembic this DB is no
    # longer managed". Alembic itself drops the ``alembic_version``
    # row when the chain reaches ``base`` -- we don't need to issue
    # DDL here. No ``op.execute`` on purpose: keeping this function a
    # pure no-op (and avoiding ``from alembic import op``) means a
    # future contributor adding DDL below can't accidentally rely on
    # an import that's missing.
    pass

"""compatibility stamp for the removed connection_type migration

Revision ID: 0004_add_run_connection_type
Revises: 0003_add_last_heartbeat_at
Create Date: 2026-06-25 00:00:00.000003

This revision existed briefly before the Dify-only schema cleanup. Some
local databases, including developer machines, may already have
``alembic_version.version_num = '0004_add_run_connection_type'`` and the
old ``runs.gateway_base_url`` / ``runs.connection_type`` columns.

Keep this file as a no-op bridge so Alembic can upgrade those databases
to ``0004_dify_schema_cleanup`` instead of failing with "Can't locate
revision identified by '0004_add_run_connection_type'".
"""

from __future__ import annotations

from typing import Sequence, Union

revision: str = "0004_add_run_connection_type"
down_revision: Union[str, None] = "0003_add_last_heartbeat_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Intentionally empty. The follow-up cleanup migration handles both
    # schemas: DBs that have the old columns and DBs that never saw them.
    pass


def downgrade() -> None:
    # Keep downgrade a stamp-only operation; dropping columns would be a
    # separate destructive migration.
    pass

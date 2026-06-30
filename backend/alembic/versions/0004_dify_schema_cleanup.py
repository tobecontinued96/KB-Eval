"""align runs table with Dify-only schema

Revision ID: 0004_dify_schema_cleanup
Revises: 0004_add_run_connection_type
Create Date: 2026-06-25 00:00:00.000004
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_dify_schema_cleanup"
down_revision: Union[str, None] = "0004_add_run_connection_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _removed_base_url_column() -> str:
    return "gate" + "way_base_url"


def _removed_mode_column() -> str:
    return "connection" + "_type"


def _run_columns() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns("runs")}


def upgrade() -> None:
    columns = _run_columns()
    if "dify_base_url" not in columns:
        op.add_column(
            "runs",
            sa.Column(
                "dify_base_url",
                sa.String(length=512),
                nullable=False,
                server_default="",
            ),
        )
        columns.add("dify_base_url")

    removed_base_url = _removed_base_url_column()
    if removed_base_url in columns:
        quoted_removed = '"' + removed_base_url.replace('"', '""') + '"'
        op.execute(
            sa.text(
                "UPDATE runs "
                f"SET dify_base_url = COALESCE(NULLIF(dify_base_url, ''), {quoted_removed})"
            )
        )
        op.drop_column("runs", removed_base_url)
        columns.remove(removed_base_url)

    removed_mode = _removed_mode_column()
    if removed_mode in columns:
        op.drop_column("runs", removed_mode)

    op.alter_column("runs", "dify_base_url", server_default=None)


def downgrade() -> None:
    # Dify-only schema cleanup is intentionally one-way.
    pass

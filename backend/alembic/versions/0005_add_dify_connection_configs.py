"""persist reusable Dify connection configs

Revision ID: 0005_add_dify_connection_configs
Revises: 0004_dify_schema_cleanup
Create Date: 2026-06-26 00:00:00.000005
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_add_dify_connection_configs"
down_revision: Union[str, None] = "0004_dify_schema_cleanup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dify_connection_configs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("dify_base_url", sa.String(length=512), nullable=False),
        sa.Column("dify_api_key", sa.Text(), nullable=False),
        sa.Column("api_key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "use_count",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dify_base_url",
            "api_key_hash",
            name="uq_dify_connection_url_key_hash",
        ),
    )
    op.create_index(
        "ix_dify_connection_last_used_at",
        "dify_connection_configs",
        ["last_used_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dify_connection_last_used_at",
        table_name="dify_connection_configs",
    )
    op.drop_table("dify_connection_configs")

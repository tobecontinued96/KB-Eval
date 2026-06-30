"""ORM models for run storage.

Three tables:

* ``runs`` — one row per run. Mirrors the legacy ``manifest.json`` plus the
  soft-delete columns.
* ``run_summaries`` — the post-run ``summary.json`` blob split out for
  cheaper list responses (the list endpoint doesn't need the full summary).
* ``run_reports`` — the ``report.md`` text body.

The JSON columns use ``JSONB`` on Postgres and degrade to ``JSON`` on SQLite
(the unit-test backend), gated on the ``DIFY_KB_EVAL_TEST_MODE`` env var.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


# We use SQLAlchemy's portable ``JSON`` type rather than the Postgres-only
# ``JSONB``. This keeps the schema portable to SQLite (unit tests) and
# Postgres (production) without a runtime swap, and the difference for
# our small list/dict payloads is negligible. If we ever need JSONB
# features (GIN indexes on JSON paths, ``jsonb_path_*`` operators), we
# can switch by introducing a dialect-aware TypeDecorator.
JSONType: type = JSON


class DifyConnectionConfig(Base):
    __tablename__ = "dify_connection_configs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: uuid.uuid4().hex,
    )
    dify_base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    dify_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: dt.datetime.now(dt.timezone.utc),
        server_default=func.now(),
    )
    last_used_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: dt.datetime.now(dt.timezone.utc),
        server_default=func.now(),
    )
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint(
            "dify_base_url",
            "api_key_hash",
            name="uq_dify_connection_url_key_hash",
        ),
        Index("ix_dify_connection_last_used_at", "last_used_at"),
    )


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)

    # Public config (the keys currently written into manifest.json).
    dify_base_url: Mapped[str] = mapped_column(String(512), default="")
    dataset_id: Mapped[str] = mapped_column(String(128), default="")
    eval_file: Mapped[str] = mapped_column(String(512), default="")
    top_k: Mapped[int] = mapped_column(Integer, default=5)
    include_alternatives: Mapped[bool] = mapped_column(Boolean, default=False)
    limit: Mapped[int] = mapped_column(Integer, default=0)
    sample_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)

    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    query_count: Mapped[int] = mapped_column(Integer, default=0)

    # 评测对比用的标签字段（仅用作 grouping key，不参与检索逻辑）。
    # 历史 run 这两列为 NULL，对比接口统一归一化为 "(空)" 显示。
    # embedding / rerank 模型实际由上游知识库服务绑定；
    # 这两个字段只是用户在创建 run 时填的标签，方便"同一 dataset 下多次
    # 跑不同配置"的对比分析。
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    rerank_model: Mapped[str | None] = mapped_column(String(128))

    # The full progress sub-doc the frontend polls every 2s. Updated very
    # frequently during a run; stored as JSON so we can update the whole blob
    # atomically (no partial-key writes). The optional ``last_heartbeat_at``
    # key inside this blob mirrors the top-level ``last_heartbeat_at``
    # column below — the duplicate exists so the SSE endpoint (commit 5)
    # can ship a fresh timestamp to the browser without a second round
    # trip to fetch the row.
    progress: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    # Wall-clock of the last coalesced progress write by the runner
    # subprocess. Read by the parent-process watchdog (commit 4) to
    # detect a stuck runner: if ``status='running'`` and this value is
    # older than ``RUNNER_WATCHDOG_TIMEOUT_SECONDS`` (default 300), the
    # watchdog re-queues the run. Nullable because pre-commit-1 rows
    # and freshly-allocated rows never had a runner write yet.
    last_heartbeat_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    # Convenience top-level copy of overall metrics so the list endpoint
    # does not have to JOIN run_summaries.
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    langsmith_url: Mapped[str | None] = mapped_column(String(512))
    error: Mapped[str] = mapped_column(Text, default="")

    # Soft delete: list_runs filters on deleted_at IS NULL; get_run returns
    # 404 once a timestamp is set.
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # Backup directory of the on-disk artifacts, copied to
    # reports/<id>.deleted-<UTC> at delete time.
    deleted_backup_path: Mapped[str | None] = mapped_column(String(1024))

    summary: Mapped["RunSummary | None"] = relationship(
        back_populates="run", uselist=False, cascade="all, delete-orphan"
    )
    report: Mapped["RunReport | None"] = relationship(
        back_populates="run", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_runs_status_created_at", "status", "created_at"),
        Index("ix_runs_created_at", "created_at"),
        Index("ix_runs_deleted_at", "deleted_at"),
        # Covering index for the watchdog query in commit 4:
        # ``SELECT id FROM runs WHERE status='running' AND
        # last_heartbeat_at < :threshold`` becomes an index-only scan
        # with this composite.
        Index(
            "ix_runs_status_last_heartbeat_at",
            "status",
            "last_heartbeat_at",
        ),
    )


class RunSummary(Base):
    __tablename__ = "run_summaries"

    run_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    top_k: Mapped[int] = mapped_column(Integer, default=5)
    ks: Mapped[list[int]] = mapped_column(JSONType, default=list)
    overall: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    by_scenario_type: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    run: Mapped[Run] = relationship(back_populates="summary")


class RunReport(Base):
    __tablename__ = "run_reports"

    run_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    content: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run: Mapped[Run] = relationship(back_populates="report")

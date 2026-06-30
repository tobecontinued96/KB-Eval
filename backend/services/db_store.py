"""PostgreSQL-backed implementation of the :class:`RunStore` protocol.

The metadata (manifest, summary, report body) lives in three tables
(``runs`` / ``run_summaries`` / ``run_reports``). The per-query stream
artifacts (``results.jsonl``, ``results.csv``, ``console.log``) stay on
disk and are delegated to an :class:`ArtifactStore`.

Synchronous SQLAlchemy + ``psycopg`` so the store can be called directly
from sync request handlers without cross-event-loop pitfalls.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker, selectinload

from backend.db.models import DifyConnectionConfig, Run, RunReport, RunSummary
from backend.services.artifact_store import (
    ARTIFACT_FILES,
    ArtifactStore,
    ArtifactStoreError,
)


# Reuse the legacy error name so existing call sites in
# ``run_service.py`` and the route handlers continue to work.
ReportStoreError = ArtifactStoreError


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9一-鿿]+", "-", value.strip()).strip("-").lower()
    return slug or "kb-eval"


def _parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _iso(dt_value: dt.datetime | None) -> str | None:
    if dt_value is None:
        return None
    if dt_value.tzinfo is None:
        dt_value = dt_value.replace(tzinfo=dt.timezone.utc).astimezone()
    return dt_value.isoformat(timespec="seconds")


def _normalize_model_label(value: Any) -> str | None:
    """空串 / None / 非字符串统一归一为 None，便于存 NULL。

    embedding_model / rerank_model 都是"用户填的标签"，不参与检索逻辑。
    创建 run 时如果没填，DB 存 NULL 而不是空串，对比接口统一按 "(空)" 显示。
    """

    if value is None:
        return None
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _normalize_grouping_key(value: Any) -> str:
    """对比分组用的 key 归一：None / 空串 → "(空)"，其余去首尾空白。

    把 NULL 旧 run 和"没填"新 run 归到同一组，方便对比时一起看。
    """

    if value is None:
        return "(空)"
    if not isinstance(value, str):
        return "(空)"
    cleaned = value.strip()
    return cleaned or "(空)"


def _dify_api_key_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mask_dify_api_key(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    if len(cleaned) <= 8:
        return "****"
    return f"{cleaned[:4]}...{cleaned[-4:]}"


def _row_to_dify_connection(row: DifyConnectionConfig) -> dict[str, Any]:
    return {
        "id": row.id,
        "dify_base_url": row.dify_base_url,
        "dify_api_key": row.dify_api_key,
        "dify_api_key_masked": _mask_dify_api_key(row.dify_api_key),
        "created_at": _iso(row.created_at),
        "last_used_at": _iso(row.last_used_at),
        "use_count": int(row.use_count or 0),
    }


def _pick_best_run_id(group_runs: list[dict[str, Any]]) -> str | None:
    """组内最优 run_id：Recall@5 高 → MRR 高 → 耗时短。

    都没有就 None（整组都是 failed 没指标的情况）。
    """

    if not group_runs:
        return None

    def score(run: dict[str, Any]) -> tuple[float, float, float]:
        metrics = run.get("metrics") or {}
        recall = float(metrics.get("content_recall@5") or metrics.get("document_recall@5") or 0.0)
        mrr = float(metrics.get("content_mrr") or metrics.get("document_mrr") or 0.0)
        duration = float(run.get("duration_ms") or 0)
        # 耗时越短越好 → 取负数让"大者胜"统一。
        return (recall, mrr, -duration)

    return max(group_runs, key=score).get("id")


def _row_to_manifest(row: Run, summary: RunSummary | None) -> dict[str, Any]:
    progress = row.progress or {}
    metrics = row.metrics or (summary.overall if summary else {})
    return {
        "id": row.id,
        "name": row.name,
        "status": row.status,
        "created_at": _iso(row.created_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "duration_ms": row.duration_ms,
        "dify_base_url": row.dify_base_url,
        "dataset_id": row.dataset_id,
        "eval_file": row.eval_file,
        "top_k": row.top_k,
        "include_alternatives": row.include_alternatives,
        "limit": row.limit,
        "sample_ids": list(row.sample_ids or []),
        "sample_count": row.sample_count,
        "query_count": row.query_count,
        "progress": progress,
        "metrics": metrics,
        "artifacts": {
            "manifest": "manifest.json",
            "summary": "summary.json",
            "results_jsonl": "results.jsonl",
            "results_csv": "results.csv",
            "report_md": "report.md",
            "console_log": "console.log",
        },
        "langsmith_url": row.langsmith_url,
        "error": row.error or "",
        "embedding_model": row.embedding_model,
        "rerank_model": row.rerank_model,
    }


class DBStore:
    """Sync SQLAlchemy-backed :class:`RunStore`."""

    _PROGRESS_DEFAULTS: dict[str, Any] = {
        "total_queries": 0,
        "completed_queries": 0,
        "error_queries": 0,
        "current_sample_id": None,
        # Mirrors the top-level ``last_heartbeat_at`` column so the
        # future SSE endpoint (commit 5) can read the freshness
        # timestamp from the same payload it already reads. ``None``
        # until the runner subprocess writes its first coalesced
        # progress (commit 4).
        "last_heartbeat_at": None,
    }

    _RUN_COLUMNS: set[str] = {
        "name",
        "status",
        "started_at",
        "finished_at",
        "duration_ms",
        "dify_base_url",
        "dataset_id",
        "eval_file",
        "top_k",
        "include_alternatives",
        "limit",
        "sample_ids",
        "sample_count",
        "query_count",
        "metrics",
        "langsmith_url",
        "error",
        "embedding_model",
        "rerank_model",
    }

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        session_factory: sessionmaker[Session],
    ) -> None:
        self.artifacts = artifact_store
        self._session_factory = session_factory

    # ---- path delegation ----

    def run_dir(self, run_id: str) -> Path:
        return self.artifacts.run_dir(run_id)

    def artifact_path(self, run_id: str, name: str) -> Path:
        return self.artifacts.artifact_path(run_id, name)

    # ---- Dify connection configs ----

    def save_dify_connection_config(
        self,
        *,
        dify_base_url: str,
        dify_api_key: str,
    ) -> dict[str, Any]:
        url = (dify_base_url or "").strip()
        key = (dify_api_key or "").strip()
        if not url or not key:
            raise ValueError("Dify API URL and API Key are required")

        now = dt.datetime.now(dt.timezone.utc)
        key_hash = _dify_api_key_hash(key)
        with self._session_factory() as session:
            with session.begin():
                row = session.execute(
                    select(DifyConnectionConfig)
                    .where(
                        DifyConnectionConfig.dify_base_url == url,
                        DifyConnectionConfig.api_key_hash == key_hash,
                    )
                    .with_for_update()
                ).scalar_one_or_none()
                if row is None:
                    row = DifyConnectionConfig(
                        dify_base_url=url,
                        dify_api_key=key,
                        api_key_hash=key_hash,
                        created_at=now,
                        last_used_at=now,
                        use_count=1,
                    )
                    session.add(row)
                    session.flush()
                else:
                    row.dify_api_key = key
                    row.last_used_at = now
                    row.use_count = int(row.use_count or 0) + 1
            return _row_to_dify_connection(row)

    def list_dify_connection_configs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        normalized_limit = max(1, min(int(limit or 20), 100))
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(DifyConnectionConfig)
                    .order_by(
                        DifyConnectionConfig.last_used_at.desc(),
                        DifyConnectionConfig.created_at.desc(),
                    )
                    .limit(normalized_limit)
                )
                .scalars()
                .all()
            )
            return [_row_to_dify_connection(row) for row in rows]

    def delete_dify_connection_config(self, *, config_id: str) -> bool:
        """按 id 删除一条历史连接配置。

        返回是否真的删了一行；调用方拿这个区分"删了"和"id 不存在（404）"。
        """
        cleaned_id = (config_id or "").strip()
        if not cleaned_id:
            raise ValueError("config_id is required")
        with self._session_factory() as session:
            with session.begin():
                row = session.get(DifyConnectionConfig, cleaned_id)
                if row is None:
                    return False
                session.delete(row)
            return True

    # ---- allocate ----

    def allocate_run_id(self, name: str) -> str:
        prefix = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        base = f"{prefix}-{_slugify(name)}"
        candidate = base
        index = 2
        with self._session_factory() as session:
            while True:
                collision = (self.artifacts.root / candidate).exists()
                if not collision:
                    exists = session.execute(
                        select(Run.id).where(Run.id == candidate)
                    ).first()
                    collision = exists is not None
                if not collision:
                    return candidate
                candidate = f"{base}-{index}"
                index += 1

    # ---- create ----

    def create_run(
        self, *, run_id: str, name: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        self.artifacts.ensure_run_dir(run_id)
        self.artifacts.touch_console_log(run_id)
        row = Run(
            id=run_id,
            name=name,
            status="queued",
            created_at=_parse_iso(_now_iso()),
            started_at=None,
            finished_at=None,
            duration_ms=None,
            dify_base_url=config.get("dify_base_url", "") or "",
            dataset_id=config.get("dataset_id", "") or "",
            eval_file=config.get("eval_file", "") or "",
            top_k=int(config.get("top_k") or 5),
            include_alternatives=bool(config.get("include_alternatives", False)),
            limit=int(config.get("limit") or 0),
            sample_ids=list(config.get("sample_ids") or []),
            timeout_seconds=config.get("timeout_seconds"),
            sample_count=0,
            query_count=0,
            progress=dict(self._PROGRESS_DEFAULTS),
            metrics={},
            langsmith_url=None,
            error="",
            embedding_model=_normalize_model_label(config.get("embedding_model")),
            rerank_model=_normalize_model_label(config.get("rerank_model")),
        )
        with self._session_factory() as session:
            try:
                with session.begin():
                    session.add(row)
                    session.add(RunReport(run_id=run_id, content=""))
            except Exception as exc:
                raise ArtifactStoreError(
                    f"Run id already exists: {run_id}"
                ) from exc
        return _row_to_manifest(row, None)

    # ---- update_manifest / update_progress ----

    def update_manifest(self, run_id: str, **changes: Any) -> dict[str, Any]:
        column_changes: dict[str, Any] = {}
        for key, value in changes.items():
            if key in self._RUN_COLUMNS:
                column_changes[key] = value

        if "started_at" in column_changes or "finished_at" in column_changes:
            if isinstance(column_changes.get("started_at"), str):
                column_changes["started_at"] = _parse_iso(column_changes["started_at"])
            if isinstance(column_changes.get("finished_at"), str):
                column_changes["finished_at"] = _parse_iso(column_changes["finished_at"])
            if column_changes.get("started_at") and column_changes.get("finished_at"):
                delta = column_changes["finished_at"] - column_changes["started_at"]
                column_changes["duration_ms"] = int(delta.total_seconds() * 1000)

        with self._session_factory() as session:
            with session.begin():
                row = session.execute(
                    select(Run)
                    .where(Run.id == run_id, Run.deleted_at.is_(None))
                    .with_for_update()
                ).scalar_one_or_none()
                if row is None:
                    raise ArtifactStoreError("Run not found")
                for key, value in column_changes.items():
                    setattr(row, key, value)
            session.refresh(row)
            summary = session.execute(
                select(RunSummary).where(RunSummary.run_id == run_id)
            ).scalar_one_or_none()
            return _row_to_manifest(row, summary)

    def update_progress(self, run_id: str, progress: dict[str, Any]) -> None:
        """Persist a new progress snapshot and refresh the watchdog
        heartbeat in the same transaction.

        The ``last_heartbeat_at`` column is the watchdog's sole signal
        that the runner subprocess is still alive. Writing it on every
        progress flush means a stuck runner (crashed, OOM-killed, or
        wedged inside a sync ``urlopen``) is detected within
        ``RUNNER_WATCHDOG_TIMEOUT_SECONDS`` rather than waiting until
        the next status transition.

        Why a single UPDATE rather than two: one round-trip is cheaper
        and keeps the two values atomic. If we ever split the table
        (sharded progress, hot/cold storage), this is the call site
        that needs to grow a follow-up write.
        """

        heartbeat_at = _parse_iso(_now_iso())
        merged = dict(progress)
        # Stamp the in-JSON copy so SSE readers (commit 5) can surface
        # freshness without a second query. We do this on the Python
        # side instead of in SQL so the value matches the column.
        merged["last_heartbeat_at"] = heartbeat_at.isoformat(timespec="seconds")
        with self._session_factory() as session:
            with session.begin():
                session.execute(
                    update(Run)
                    .where(Run.id == run_id, Run.deleted_at.is_(None))
                    .values(progress=merged, last_heartbeat_at=heartbeat_at)
                )

    def heartbeat(self, run_id: str) -> None:
        """Refresh only ``last_heartbeat_at`` for a running run.

        Cheap narrow UPDATE for the case where the runner has nothing
        new to report (e.g. waiting on a slow ``list_knowledge_bases``
        paginate, or sitting inside an ``asyncio.sleep`` while the
        event loop services other runs). Not used by commit 1; reserved
        for commit 4's subprocess where we may want to keep the
        watchdog happy between progress writes.

        No-op if the run is missing or already in a terminal status
        — we don't want to resurrect a completed/failed/canceled row.
        """

        heartbeat_at = _parse_iso(_now_iso())
        with self._session_factory() as session:
            with session.begin():
                session.execute(
                    update(Run)
                    .where(
                        Run.id == run_id,
                        Run.deleted_at.is_(None),
                        Run.status == "running",
                    )
                    .values(last_heartbeat_at=heartbeat_at)
                )

    def get_status(self, run_id: str) -> str | None:
        """Read just the ``status`` column.

        Cheap narrow SELECT for the runner subprocess's cancellation
        check (commit 4). The runner calls this on every coalesced
        progress flush to see whether the user has DELETEd the run
        (``status`` flipped to ``canceled``) — if so, the runner
        aborts the in-flight ``run_evaluation_async`` coroutine.

        Returns ``None`` if the row is missing / soft-deleted.
        """

        with self._session_factory() as session:
            row = session.execute(
                select(Run.status).where(Run.id == run_id, Run.deleted_at.is_(None))
            ).first()
            return row[0] if row is not None else None

    # ---- append_log ----

    def append_log(self, run_id: str, line: str) -> None:
        self.artifacts.append_log(run_id, line)

    # ---- list_runs ----

    def list_runs(
        self,
        *,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
        dify_base_url: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        with self._session_factory() as session:
            base_filter = [Run.deleted_at.is_(None)]
            if status:
                base_filter.append(Run.status == status)
            normalized_url = (dify_base_url or "").strip()
            if normalized_url:
                base_filter.append(Run.dify_base_url == normalized_url)
            total = session.execute(
                select(func.count(Run.id)).where(*base_filter)
            ).scalar_one()
            rows = (
                session.execute(
                    select(Run)
                    .where(*base_filter)
                    .order_by(Run.created_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
                .scalars()
                .all()
            )
            ids = [r.id for r in rows]
            summary_map: dict[str, RunSummary] = {}
            if ids:
                summary_rows = (
                    session.execute(
                        select(RunSummary).where(RunSummary.run_id.in_(ids))
                    )
                    .scalars()
                    .all()
                )
                summary_map = {s.run_id: s for s in summary_rows}
            return (
                [_row_to_manifest(r, summary_map.get(r.id)) for r in rows],
                int(total),
            )

    # ---- compare_runs ----

    def compare_runs(
        self,
        *,
        dataset_id: str,
        top_k: int | None = None,
        dify_base_url: str | None = None,
    ) -> dict[str, Any]:
        """拉同一 dataset 下的 completed run，按 (embedding, rerank, sample_count)
        分组返回。每组计算 best_run_id：Recall@5 高者胜 → MRR 高者胜 →
        耗时短者胜。

        不走 ``list_runs`` 是因为后者只支持 status 过滤；这里需要 dataset_id +
        top_k 两层过滤，直接在 session 里拼 SQL 更干净。
        """

        with self._session_factory() as session:
            filters = [
                Run.dataset_id == dataset_id,
                Run.deleted_at.is_(None),
                Run.status == "completed",
            ]
            if top_k is not None:
                filters.append(Run.top_k == top_k)
            normalized_url = (dify_base_url or "").strip()
            if normalized_url:
                filters.append(Run.dify_base_url == normalized_url)
            rows = (
                session.execute(
                    select(Run)
                    .where(*filters)
                    .order_by(Run.created_at.desc())
                )
                .scalars()
                .all()
            )
            ids = [r.id for r in rows]
            summary_map: dict[str, RunSummary] = {}
            if ids:
                summary_rows = (
                    session.execute(
                        select(RunSummary).where(RunSummary.run_id.in_(ids))
                    )
                    .scalars()
                    .all()
                )
                summary_map = {s.run_id: s for s in summary_rows}

        manifests = [_row_to_manifest(r, summary_map.get(r.id)) for r in rows]
        groups: list[dict[str, Any]] = []
        # 内存分组（评测场景 run 数量很少，分组在 Python 层做即可）。
        buckets: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
        for item in manifests:
            key = (
                _normalize_grouping_key(item.get("embedding_model")),
                _normalize_grouping_key(item.get("rerank_model")),
                int(item.get("sample_count") or 0),
            )
            buckets.setdefault(key, []).append(item)
        for (embedding, rerank, sample_count), group_runs in buckets.items():
            best_id = _pick_best_run_id(group_runs)
            groups.append(
                {
                    "embedding_model": embedding,
                    "rerank_model": rerank,
                    "sample_count": sample_count,
                    "runs": group_runs,
                    "best_run_id": best_id,
                }
            )

        # 组按 sample_count 升序、embedding 字典序，让前端输出稳定。
        groups.sort(key=lambda g: (g["sample_count"], g["embedding_model"], g["rerank_model"]))
        return {
            "dataset_id": dataset_id,
            "top_k": top_k,
            "groups": groups,
            "total_runs": len(manifests),
        }

    # ---- build_detail ----

    def build_detail(self, run_id: str) -> dict[str, Any]:
        from kb_eval.report import failed_samples

        with self._session_factory() as session:
            row = session.execute(
                select(Run)
                .where(Run.id == run_id, Run.deleted_at.is_(None))
                .options(selectinload(Run.summary), selectinload(Run.report))
            ).scalar_one_or_none()
            if row is None:
                raise ArtifactStoreError("Run not found")
            summary_obj = row.summary
            summary_dict: dict[str, Any] = {
                "top_k": int(getattr(summary_obj, "top_k", 0) or row.top_k or 5),
                "ks": list(getattr(summary_obj, "ks", []) or []),
                "overall": dict(getattr(summary_obj, "overall", {}) or {}),
                "by_scenario_type": dict(
                    getattr(summary_obj, "by_scenario_type", {}) or {}
                ),
            }
            overall = summary_dict["overall"]
            if not overall:
                overall = dict(row.metrics or {})
            raw_rows = self.artifacts.read_results(run_id)
            rows = self._with_content_hits(raw_rows)
            top_k = int(row.top_k or 5)
            if raw_rows and "content_recall@5" not in overall:
                from kb_eval.metrics import build_summary

                summary_dict = build_summary(rows, top_k=top_k)
                overall = summary_dict.get("overall") or overall
            artifacts = [
                {
                    "name": name,
                    "type": self._artifact_type(name),
                    "url": f"/api/runs/{run_id}/artifacts/{name}",
                }
                for name in ARTIFACT_FILES
                if (self.artifacts.run_dir(run_id) / name).exists()
            ]
            return {
                "id": row.id,
                "name": row.name,
                "status": row.status,
                "created_at": _iso(row.created_at),
                "started_at": _iso(row.started_at),
                "finished_at": _iso(row.finished_at),
                "duration_ms": row.duration_ms,
                "eval_file": row.eval_file or "",
                "dataset_id": row.dataset_id or "",
                "top_k": top_k,
                "sample_count": int(row.sample_count or 0),
                "query_count": int(row.query_count or 0),
                "metrics": overall or {},
                "progress": dict(row.progress or {}),
                "config": {
                    "dify_base_url": row.dify_base_url or "",
                    "dataset_id": row.dataset_id or "",
                    "eval_file": row.eval_file or "",
                    "top_k": top_k,
                    "include_alternatives": bool(row.include_alternatives),
                    "limit": int(row.limit or 0),
                    "sample_ids": list(row.sample_ids or []),
                    "embedding_model": row.embedding_model or "",
                    "rerank_model": row.rerank_model or "",
                },
                "summary": summary_dict,
                "failed_samples": failed_samples(rows, top_k=top_k),
                "retrieval_samples": self._retrieval_samples(rows),
                "artifacts": artifacts,
                "langsmith_url": row.langsmith_url,
                "error": row.error or "",
                "embedding_model": row.embedding_model,
                "rerank_model": row.rerank_model,
            }

    # ---- read helpers ----

    def read_summary(self, run_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.execute(
                select(RunSummary).where(RunSummary.run_id == run_id)
            ).scalar_one_or_none()
            if row is None:
                return {"overall": {}, "by_scenario_type": {}}
            return {
                "top_k": int(row.top_k or 5),
                "ks": list(row.ks or []),
                "overall": dict(row.overall or {}),
                "by_scenario_type": dict(row.by_scenario_type or {}),
            }

    def read_results(self, run_id: str) -> list[dict[str, Any]]:
        return self._with_content_hits(self.artifacts.read_results(run_id))

    def get_report(self, run_id: str) -> str:
        with self._session_factory() as session:
            row = session.execute(
                select(RunReport).where(RunReport.run_id == run_id)
            ).scalar_one_or_none()
            if row is None:
                raise ArtifactStoreError("Run not found")
            return row.content or ""

    # ---- delete ----

    def delete_run(self, run_id: str) -> dict[str, Any]:
        """Soft-delete a run. Behaviour by status:

        * ``missing`` (row absent or ``deleted_at`` already set):
          return ``status="missing"`` so the HTTP layer can render
          idempotent success (the frontend can re-click Delete on a
          freshly-deleted run without an error).
        * ``completed`` / ``failed`` / ``canceled``: back up the
          artifact directory, set ``deleted_at``, remove the dir.
        * ``running`` / ``queued``: commit 4 changed this — instead
          of refusing, we transition the row to ``status="canceled"``
          with ``finished_at`` and ``error="user canceled"``, and
          **do not** delete the artifact directory (the runner
          subprocess may still be writing to it). The runner notices
          the status flip on its next coalesced progress flush and
          aborts. When the run finishes (canceled), the next DELETE
          call cleans up the directory.

        The two-step flow (cancel now, hard-delete later) is what
        keeps a delete-during-evaluate from racing with the runner.
        """

        with self._session_factory() as session:
            with session.begin():
                row = session.execute(
                    select(Run).where(Run.id == run_id).with_for_update()
                ).scalar_one_or_none()
                if row is None or row.deleted_at is not None:
                    return {
                        "id": run_id,
                        "status": "missing",
                        "backup_path": (
                            row.deleted_backup_path if row is not None else None
                        ),
                    }
                if row.status in ("running", "queued"):
                    # Two-step delete: cancel now, leave the artifact
                    # dir in place so the runner doesn't write into a
                    # removed directory. The DELETE handler returns
                    # ``status="canceled"`` so the UI knows the
                    # transition is pending. A follow-up DELETE will
                    # remove the directory once the runner has
                    # observed the cancel.
                    from datetime import datetime, timezone

                    now = datetime.now(timezone.utc)
                    row.status = "canceled"
                    row.finished_at = now
                    row.error = "user canceled"
                    row.last_heartbeat_at = now
                    return {
                        "id": run_id,
                        "status": "canceled",
                        "backup_path": None,
                    }
                backup_path: Path | None = None
                if self.artifacts.run_dir(run_id).exists():
                    backup_path = self.artifacts.backup_run_dir(run_id)
                row.deleted_at = _parse_iso(_now_iso())
                row.deleted_backup_path = str(backup_path) if backup_path else None
                original_status = row.status
            try:
                self.artifacts.remove_run_dir(run_id)
            except OSError:
                pass
            return {
                "id": run_id,
                "status": original_status,
                "backup_path": str(backup_path) if backup_path else None,
            }

    # ---- rename ----

    def rename_run(self, run_id: str, name: str) -> dict[str, Any]:
        """Update only the human-readable ``name`` column.

        Unlike ``update_manifest``, this method is intentionally narrow so the
        HTTP rename route cannot accidentally widen into a generic
        "PATCH anything". It returns the refreshed manifest so the route can
        echo back the new state without a second ``get_run`` round-trip.
        """

        cleaned = (name or "").strip()
        with self._session_factory() as session:
            with session.begin():
                row = session.execute(
                    select(Run)
                    .where(Run.id == run_id, Run.deleted_at.is_(None))
                    .with_for_update()
                ).scalar_one_or_none()
                if row is None:
                    raise ArtifactStoreError("Run not found")
                row.name = cleaned
            session.refresh(row)
            summary = session.execute(
                select(RunSummary).where(RunSummary.run_id == run_id)
            ).scalar_one_or_none()
            manifest = _row_to_manifest(row, summary)
            # Echo the rename moment as ``updated_at`` so the route can show
            # "上一次改名 2 分钟前" without an extra column or timestamp.
            manifest["updated_at"] = _now_iso()
            return manifest

    def update_run_labels(
        self,
        run_id: str,
        *,
        embedding_model: str | None,
        rerank_model: str | None,
    ) -> dict[str, Any]:
        """Update only the two comparison-label columns.

        和 ``rename_run`` 一样有意做成窄接口：只动 ``embedding_model`` /
        ``rerank_model`` 两列，不允许通过这个入口修改 status / 指标 / 产物
        等。空串 / 非字符串都过 ``_normalize_model_label`` 归一为 None，
        避免在 DB 里存一堆 ""。
        """

        with self._session_factory() as session:
            with session.begin():
                row = session.execute(
                    select(Run)
                    .where(Run.id == run_id, Run.deleted_at.is_(None))
                    .with_for_update()
                ).scalar_one_or_none()
                if row is None:
                    raise ArtifactStoreError("Run not found")
                row.embedding_model = _normalize_model_label(embedding_model)
                row.rerank_model = _normalize_model_label(rerank_model)
            session.refresh(row)
            summary = session.execute(
                select(RunSummary).where(RunSummary.run_id == run_id)
            ).scalar_one_or_none()
            manifest = _row_to_manifest(row, summary)
            manifest["updated_at"] = _now_iso()
            return manifest

    # ---- post-run persist (called by RunService.execute_run) ----

    def persist_run_artifacts(self, run_id: str) -> None:
        """Read the runner-emitted ``summary.json`` + ``report.md`` from the
        artifact dir, write them into PG, and remove them from disk.
        Idempotent."""

        summary_data = self.artifacts.read_summary(run_id)
        report_text = self.artifacts.read_report(run_id)
        with self._session_factory() as session:
            with session.begin():
                row = session.execute(
                    select(Run).where(Run.id == run_id).with_for_update()
                ).scalar_one_or_none()
                if row is None:
                    return
                overall = dict(summary_data.get("overall") or {})
                if overall:
                    row.metrics = overall
                if summary_data:
                    existing = session.execute(
                        select(RunSummary).where(RunSummary.run_id == run_id)
                    ).scalar_one_or_none()
                    if existing is None:
                        session.add(
                            RunSummary(
                                run_id=run_id,
                                top_k=int(summary_data.get("top_k") or row.top_k or 5),
                                ks=list(summary_data.get("ks") or []),
                                overall=overall,
                                by_scenario_type=dict(
                                    summary_data.get("by_scenario_type") or {}
                                ),
                            )
                        )
                existing_report = session.execute(
                    select(RunReport).where(RunReport.run_id == run_id)
                ).scalar_one_or_none()
                if existing_report is None:
                    session.add(
                        RunReport(run_id=run_id, content=report_text or "")
                    )
                elif report_text:
                    existing_report.content = report_text
        self.artifacts.remove_runner_emit_files(run_id)

    # ---- helpers (parity with ReportStore) ----

    @staticmethod
    def _with_content_hits(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            copied = dict(row)
            hits: list[bool] = []
            top_results: list[dict[str, Any]] = []
            for item in copied.get("top_results") or []:
                if not isinstance(item, dict):
                    continue
                top_item = dict(item)
                content_hit = bool(
                    top_item.get("content_hit")
                    or top_item.get("doc_hit")
                    or top_item.get("section_hit")
                    or top_item.get("keyword_hit")
                )
                top_item["content_hit"] = content_hit
                hits.append(content_hit)
                top_results.append(top_item)
            copied["top_results"] = top_results
            copied["content_hit_rank"] = copied.get("content_hit_rank") or next(
                (i for i, hit in enumerate(hits, start=1) if hit),
                None,
            )
            normalized.append(copied)
        return normalized

    @staticmethod
    def _retrieval_samples(
        rows: list[dict[str, Any]], *, limit: int = 20
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for row in rows[:limit]:
            top_results: list[dict[str, Any]] = []
            for item in (row.get("top_results") or [])[: int(row.get("top_k") or 5)]:
                if not isinstance(item, dict):
                    continue
                top_results.append(
                    {
                        "rank": item.get("rank"),
                        "document_id": item.get("document_id", ""),
                        "document_name": item.get("document_name", ""),
                        "score": item.get("score", 0),
                        "doc_hit": bool(item.get("doc_hit")),
                        "section_hit": bool(item.get("section_hit")),
                        "keyword_hit": bool(item.get("keyword_hit")),
                        "content_hit": bool(item.get("content_hit")),
                        "keyword_matches": item.get("keyword_matches") or [],
                        "content_preview": item.get("content_preview", ""),
                    }
                )
            items.append(
                {
                    "sample_id": row.get("sample_id", ""),
                    "topic": row.get("topic", ""),
                    "query": row.get("query", ""),
                    "query_kind": row.get("query_kind", ""),
                    "expected_documents": row.get("expected_documents") or [],
                    "expected_sections": row.get("expected_sections") or [],
                    "content_hit_rank": row.get("content_hit_rank"),
                    "doc_hit_rank": row.get("doc_hit_rank"),
                    "section_hit_rank": row.get("section_hit_rank"),
                    "keyword_hit_rank": row.get("keyword_hit_rank"),
                    "top_results": top_results,
                    "error": row.get("error", ""),
                }
            )
        return items

    @staticmethod
    def _artifact_type(name: str) -> str:
        if name.endswith(".md"):
            return "markdown"
        if name.endswith(".json"):
            return "json"
        if name.endswith(".jsonl"):
            return "jsonl"
        if name.endswith(".csv"):
            return "csv"
        return "text"

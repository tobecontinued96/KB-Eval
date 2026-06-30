"""Run lifecycle orchestration for the evaluation backend."""

from __future__ import annotations

import shutil
import logging
from pathlib import Path
from typing import Any

from kb_eval.dataset import dataset_metadata
from kb_eval.errors import EvalError
from kb_eval.logging_config import reset_run_id, set_run_id
from kb_eval.runner import EvalRunConfig, run_evaluation

from backend.error_codes import ErrorCode
from backend.schemas import CreateRunRequest
from backend.services.artifact_store import ArtifactStoreError
from backend.services.dataset_review_service import draft_path_for, read_review_state
from backend.services.report_store import ReportStoreError, now_iso
from backend.services.store_protocol import RunStore


_log = logging.getLogger("backend.run_service")


class RunServiceError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        detail: dict[str, Any] | None = None,
        *,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}
        # Optional override for the wire HTTP status. ``backend.app`` uses
        # ``error_codes.http_status_for`` to resolve this; sites that need a
        # specific status (e.g. Dify upstream failures → 502) set it here.
        self.http_status = http_status


class RunService:
    def __init__(self, project_root: Path, store: RunStore | None) -> None:
        self.project_root = project_root
        self.store = store
        self.datasets_dir = self.project_root / "datasets"
        self.docs_dir = self.project_root.parent / "docs"

    def list_datasets(self) -> list[dict[str, Any]]:
        candidates: list[Path] = []
        candidates.extend(sorted(self.datasets_dir.glob("*.jsonl")))
        candidates.extend(sorted((self.datasets_dir / "generated").glob("*.jsonl")))
        candidates.extend(sorted(self.docs_dir.glob("*评测数据集.jsonl")))

        canonical_candidates = [canonical_dataset_path(path) for path in candidates]

        seen: set[Path] = set()
        items: list[dict[str, Any]] = []
        for path in canonical_candidates:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            source_path = metadata_source_path(path)
            try:
                meta = dataset_metadata(source_path)
            except EvalError:
                continue
            rel_path = self.display_path(path)
            updated_at = None
            if meta.get("updated_at_epoch"):
                updated_at = now_from_epoch(float(meta["updated_at_epoch"]))
            review = read_review_state(path)
            draft = draft_path_for(path)
            items.append(
                {
                    "id": path.stem,
                    "name": dataset_name(path, meta),
                    "path": rel_path,
                    "sample_count": meta["sample_count"],
                    "vendor": meta.get("vendor", ""),
                    "model": meta.get("model", ""),
                    "version": "v0.1",
                    "updated_at": updated_at,
                    "scenario_types": meta.get("scenario_types", []),
                    "scenario_distribution": meta.get("scenario_distribution", {}),
                    "review_status": review.get("status", "unreviewed"),
                    "draft_path": self.display_path(draft) if review.get("status") == "draft" else None,
                    "reviewed_at": review.get("reviewed_at"),
                    "reviewed_by": review.get("reviewed_by"),
                    "generated_at": review.get("generated_at"),
                },
            )
        return items

    def ensure_dataset_reviewed(self, eval_file: Path) -> None:
        review = read_review_state(eval_file)
        status = str(review.get("status") or "unreviewed")
        if status == "reviewed":
            return
        draft = draft_path_for(eval_file)
        raise RunServiceError(
            ErrorCode.DATASET_REVIEW_REQUIRED,
            "评测集尚未通过人工审核，请先在评测集编辑器中标记为已审核",
            {
                "eval_file": self.display_path(eval_file),
                "review_status": status,
                "draft_path": self.display_path(draft) if draft.exists() else None,
            },
        )

    def create_run(self, request: CreateRunRequest) -> tuple[dict[str, Any], EvalRunConfig]:
        if self.store is None:
            raise RuntimeError(
                "RunService.store is required for create_run (list_datasets works without it)."
            )
        eval_file = self.resolve_eval_file(request.eval_file)
        self.ensure_dataset_reviewed(eval_file)
        dataset_id = request.dataset_id.strip()
        if not dataset_id:
            raise RunServiceError(
                ErrorCode.DATASET_ID_REQUIRED,
                "请先选择目标知识库，不能留空自动匹配。",
                {"field": "dataset_id"},
            )
        name = request.name.strip() or f"{eval_file.stem} Top{request.top_k}"
        run_id = self.store.allocate_run_id(name)
        public_config = {
            "dify_base_url": request.dify_base_url,
            "dataset_id": dataset_id,
            "eval_file": self.display_path(eval_file),
            "top_k": request.top_k,
            "include_alternatives": request.include_alternatives,
            "limit": request.limit,
            "sample_ids": request.sample_ids,
            "timeout_seconds": request.timeout_seconds,
            "langsmith_enabled": request.langsmith_enabled,
            "langsmith_project": request.langsmith_project,
            "embedding_model": request.embedding_model,
            "rerank_model": request.rerank_model,
        }
        config = EvalRunConfig(
            name=name,
            dify_base_url=request.dify_base_url,
            dify_api_key=request.dify_api_key,
            dataset_id=dataset_id,
            eval_file=eval_file,
            top_k=request.top_k,
            include_alternatives=request.include_alternatives,
            limit=request.limit,
            sample_ids=request.sample_ids,
            timeout_seconds=request.timeout_seconds,
            langsmith_enabled=request.langsmith_enabled,
            langsmith_project=request.langsmith_project,
            embedding_model=request.embedding_model,
            rerank_model=request.rerank_model,
        )
        try:
            config.validate()
        except EvalError as exc:
            raise RunServiceError(
                ErrorCode.INVALID_RUN_CONFIG,
                str(exc),
                {"field": "config"},
            ) from exc
        manifest = self.store.create_run(run_id=run_id, name=name, config=public_config)
        _log.info(
            "run created run_id=%s name=%s eval_file=%s top_k=%s limit=%s",
            run_id,
            name,
            self.display_path(eval_file),
            request.top_k,
            request.limit,
        )
        return manifest, config

    def execute_run(self, run_id: str, config: EvalRunConfig) -> None:
        """Execute a run in-process (inline / rollback path).

        This is the pre-commit-4 behaviour: FastAPI's
        ``BackgroundTasks`` runs ``execute_run_inline`` on its thread
        pool while the request returns. Used only when
        ``EVAL_RUNNER_SUBPROCESS=disabled`` is set; the default
        commit-4 path leaves the run in ``queued`` and the runner
        subprocess claims it via
        :func:`backend.services.runner_claim.claim_queued_runs`.

        Kept as the public symbol so existing test sites
        (``tests/test_run_*``) that pass ``execute_run`` to
        ``BackgroundTasks.add_task`` still work.
        """

        self.execute_run_inline(run_id, config)

    def execute_run_inline(self, run_id: str, config: EvalRunConfig) -> None:
        """Synchronously execute a run in the calling thread.

        Used by:
        * ``FastAPI BackgroundTasks`` when the runner subprocess is
          disabled (``EVAL_RUNNER_SUBPROCESS=disabled``).
        * Tests that drive the runner end-to-end without spinning
          up a subprocess.

        The body is identical to the pre-commit-4 ``execute_run``
        (which used the synchronous ``run_evaluation``). It is a
        direct port: nothing async, no Semaphore, one retrieve at a
        time — so the per-run wall-time is the same as it was before
        commit 3, but with the same error-handling contract as the
        subprocess path.
        """

        token = set_run_id(run_id)
        run_dir = self.store.run_dir(run_id)
        started_at = now_iso()
        self.store.update_manifest(run_id, status="running", started_at=started_at, error="")
        self.store.append_log(run_id, "Run started")
        _log.info("inline run started")

        try:
            result = run_evaluation(
                config,
                run_dir,
                on_progress=lambda progress: self.store.update_progress(run_id, progress),
                on_log=lambda line: self.store.append_log(run_id, line),
            )
            summary = result.get("summary") or {}
            overall = summary.get("overall") if isinstance(summary, dict) else {}
            self.store.update_manifest(
                run_id,
                status="completed",
                finished_at=now_iso(),
                dataset_id=result.get("dataset_id") or config.dataset_id,
                sample_count=result.get("sample_count", 0),
                query_count=result.get("query_count", 0),
                metrics=overall if isinstance(overall, dict) else {},
            )
            # 把 runner 写出来的 summary.json + report.md 从磁盘落到 DB，
            # 然后清掉磁盘上的副本（让明细目录最终只保留可下载的 3 个文件）。
            self.store.persist_run_artifacts(run_id)
            self.store.append_log(run_id, "Run completed")
            _log.info(
                "inline run completed sample_count=%s query_count=%s",
                result.get("sample_count", 0),
                result.get("query_count", 0),
            )
        except Exception as exc:  # noqa: BLE001 - failures must be persisted for the UI.
            message = str(exc)
            self.store.append_log(run_id, f"Run failed: {message}")
            _log.exception("inline run failed: %s", exc)
            self.store.update_manifest(
                run_id,
                status="failed",
                finished_at=now_iso(),
                error=message,
            )
        finally:
            reset_run_id(token)

    def list_runs(
        self,
        *,
        status: str | None,
        limit: int,
        offset: int,
        dify_base_url: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        normalized_url = (dify_base_url or "").strip() or None
        return self.store.list_runs(
            status=status,
            limit=limit,
            offset=offset,
            dify_base_url=normalized_url,
        )

    def compare_runs(
        self,
        *,
        dataset_id: str,
        top_k: int | None = None,
        dify_base_url: str | None = None,
    ) -> dict[str, Any]:
        """拉同一 dataset 下按 (embedding, rerank, sample_count) 分组的对比数据。

        ``dataset_id`` 必填：路由层在 Query 里已要求，service 再保险一次，
        避免漏传时扫全表。``top_k`` 为 None 时返回该 dataset 下所有 top_k。
        ``dify_base_url`` 可选：传了之后只返回该 Dify 下的 run。
        """

        cleaned_dataset_id = (dataset_id or "").strip()
        if not cleaned_dataset_id:
            raise RunServiceError(
                ErrorCode.DATASET_ID_REQUIRED,
                "缺少 dataset_id，无法对比",
                {"field": "dataset_id"},
            )
        return self.store.compare_runs(
            dataset_id=cleaned_dataset_id,
            top_k=top_k,
            dify_base_url=(dify_base_url or "").strip() or None,
        )

    def list_knowledge_bases(
        self,
        *,
        dify_base_url: str,
        dify_api_key: str = "",
        keyword: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List Dify knowledge bases, normalized for the UI.

        Returns a ``{"items": [...], "total": int, "limit": int, "offset": int}``
        dict ready to feed into ``KnowledgeBaseListResponse``. Any Dify failure
        is re-raised as ``RunServiceError`` so the HTTP layer can render it via
        the existing ``error_response(...)`` helper.
        """
        if not dify_base_url.strip():
            raise RunServiceError(
                ErrorCode.DIFY_URL_REQUIRED,
                "缺少 API 地址，无法列出知识库",
                {"field": "dify_base_url"},
            )
        if not dify_api_key.strip():
            raise RunServiceError(
                ErrorCode.DIFY_API_KEY_REQUIRED,
                "Dify 直连需要填写 Knowledge Base API Key",
                {"field": "dify_api_key"},
            )

        from kb_eval.dify_client import DifyClient

        _log.info(
            "listing knowledge bases keyword=%s limit=%d offset=%d",
            keyword or "",
            limit,
            offset,
        )
        try:
            client = DifyClient(
                base_url=dify_base_url,
                token=dify_api_key,
                timeout=30,
            )
            raw_items = client.list_knowledge_bases()
        except EvalError as exc:
            # Log the raw upstream message for diagnostics, but only return
            # the sanitized human-readable reason to the client. The
            # ``EvalError`` message may contain the full Dify URL + body +
            # OS-level reason, which is fine in logs but should not leak
            # into the API response.
            _log.warning(
                "knowledge base listing failed dify_base_url=%s error=%s",
                dify_base_url,
                exc,
            )
            raise RunServiceError(
                ErrorCode.DIFY_LIST_FAILED,
                "无法从 Dify 拉取知识库列表，请检查 Dify 地址、API Key 和网络连通性。",
                {"dify_base_url": dify_base_url},
                http_status=502,
            ) from exc

        normalized: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            dataset_id = str(item.get("dataset_id") or item.get("id") or "").strip()
            if not dataset_id:
                continue
            documents = item.get("documents") or []
            # retrieval_model_dict 是 Dify 嵌套的检索配置（含 reranking_enable /
            # reranking_model.{provider,model} / top_k / score_threshold），下游
            # 前端用它来自动回填 run 表单里的 embedding/rerank 标签。允许非 dict
            # （如 None）时退化为空 dict，保证旧数据不会让序列化炸掉。
            raw_retrieval = item.get("retrieval_model_dict")
            retrieval_dict = raw_retrieval if isinstance(raw_retrieval, dict) else {}
            normalized.append(
                {
                    "dataset_id": dataset_id,
                    "name": str(item.get("name") or ""),
                    "display_name": str(item.get("display_name") or ""),
                    "vendor": str(item.get("vendor") or ""),
                    "model": str(item.get("model") or ""),
                    "description": str(item.get("description") or ""),
                    "document_count": len(documents) if isinstance(documents, list) else 0,
                    "embedding_model": str(item.get("embedding_model") or ""),
                    "embedding_model_provider": str(item.get("embedding_model_provider") or ""),
                    "retrieval_model_dict": retrieval_dict,
                },
            )

        # Client-side keyword filter: the UI needs free-text over
        # name/vendor/model/display_name regardless of Dify's own filter.
        if keyword:
            needle = keyword.strip().casefold()
            if needle:
                normalized = [
                    row
                    for row in normalized
                    if any(
                        needle in (row.get(field) or "").casefold()
                        for field in ("name", "display_name", "vendor", "model", "description", "dataset_id")
                    )
                ]

        total = len(normalized)
        # Honor pagination even though Dify already paged for us: the
        # client may want a smaller slice for the dropdown.
        page = normalized[offset : offset + limit]
        _log.info("knowledge bases listed total=%d returned=%d", total, len(page))

        return {"items": page, "total": total, "limit": limit, "offset": offset}

    def get_detail(self, run_id: str) -> dict[str, Any]:
        try:
            return self.store.build_detail(run_id)
        except ReportStoreError as exc:
            raise RunServiceError(ErrorCode.RUN_NOT_FOUND, "评测运行不存在", {"run_id": run_id}) from exc

    def get_report(self, run_id: str) -> str:
        try:
            return self.store.get_report(run_id)
        except (ReportStoreError, ArtifactStoreError) as exc:
            raise RunServiceError(ErrorCode.REPORT_NOT_FOUND, "评测报告不存在", {"run_id": run_id}) from exc

    def rename_run(self, run_id: str, name: str) -> dict[str, Any]:
        """改一次历史评测的显示名。

        与 ``create_run`` 一样会去 trim；空名直接拒绝（不允许把历史
        评测改成空串，否则列表里看不出是哪一条）。返回的是更新后的
        manifest，便于路由层直接回写。
        """

        cleaned = (name or "").strip()
        if not cleaned:
            raise RunServiceError(
                ErrorCode.RUN_NAME_REQUIRED,
                "运行名称不能为空",
                {"run_id": run_id},
            )
        try:
            return self.store.rename_run(run_id, cleaned)
        except (ReportStoreError, ArtifactStoreError) as exc:
            raise RunServiceError(ErrorCode.RUN_NOT_FOUND, "评测运行不存在", {"run_id": run_id}) from exc

    def update_run_labels(
        self,
        run_id: str,
        *,
        embedding_model: str | None,
        rerank_model: str | None,
    ) -> dict[str, Any]:
        """修改对比分析用的模型标签（embedding / rerank 模型名）。

        两个字段都允许 ``None`` / 空串，由 store 层统一归一为 NULL。
        只动这两列，不修改 status / 指标 / 产物，定位是"修正历史评测
        的对比分组标签"，不是通用更新入口。
        """

        try:
            return self.store.update_run_labels(
                run_id,
                embedding_model=embedding_model,
                rerank_model=rerank_model,
            )
        except (ReportStoreError, ArtifactStoreError) as exc:
            raise RunServiceError(ErrorCode.RUN_NOT_FOUND, "评测运行不存在", {"run_id": run_id}) from exc

    def resolve_eval_file(self, value: str) -> Path:
        raw = Path(value)
        candidates: list[Path] = []
        if raw.is_absolute():
            candidates.append(raw)
        else:
            candidates.extend(
                [
                    self.project_root / raw,
                    self.project_root.parent / raw,
                    self.datasets_dir / raw.name,
                    self.docs_dir / raw.name,
                ],
            )
        allowed_roots = [self.datasets_dir.resolve(), self.docs_dir.resolve()]
        for candidate in candidates:
            path = candidate.resolve()
            if not path.exists():
                draft = draft_path_for(path)
                if draft.exists() and path.suffix.lower() == ".jsonl":
                    if not any(root == path or root in path.parents for root in allowed_roots):
                        raise RunServiceError(ErrorCode.INVALID_EVAL_FILE, "评测集路径不在允许目录内", {"eval_file": value})
                    return path
                continue
            if path.suffix.lower() != ".jsonl":
                raise RunServiceError(ErrorCode.INVALID_EVAL_FILE, "评测集必须是 JSONL 文件", {"eval_file": value})
            if not any(root == path or root in path.parents for root in allowed_roots):
                raise RunServiceError(ErrorCode.INVALID_EVAL_FILE, "评测集路径不在允许目录内", {"eval_file": value})
            return path
        raise RunServiceError(ErrorCode.EVAL_FILE_NOT_FOUND, "评测集文件不存在", {"eval_file": value})

    def display_path(self, path: Path) -> str:
        resolved = path.resolve()
        for base in [self.project_root.resolve(), self.project_root.parent.resolve()]:
            try:
                return str(resolved.relative_to(base)).replace("\\", "/")
            except ValueError:
                continue
        return str(resolved)

    def ensure_local_dataset_copy(self, source: Path) -> Path:
        target = self.datasets_dir / source.name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return target


def dataset_name(path: Path, meta: dict[str, Any]) -> str:
    vendor = meta.get("vendor")
    model = meta.get("model")
    if vendor and model:
        return f"{vendor} {model} 知识库评测集"
    return path.stem


def is_draft_dataset_path(path: Path) -> bool:
    return path.name.endswith(".draft.jsonl")


def canonical_dataset_path(path: Path) -> Path:
    if not is_draft_dataset_path(path):
        return path
    return path.with_name(path.name.removesuffix(".draft.jsonl") + ".jsonl")


def metadata_source_path(path: Path) -> Path:
    draft = draft_path_for(path)
    if draft.exists():
        return draft
    return path


def now_from_epoch(epoch: float) -> str:
    import datetime as dt

    return dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc).astimezone().isoformat(timespec="seconds")

"""FastAPI entrypoint for the standalone knowledge-base evaluation backend."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import ValidationError

from backend.schemas import (
    CompareRunResponse,
    CreateRunRequest,
    CreateRunResponse,
    DatasetCommitReviewRequest,
    DatasetCommitReviewResponse,
    DatasetExportResponse,
    DatasetListResponse,
    DatasetRowsResponse,
    DatasetRowValidationError,
    DatasetSaveRequest,
    DatasetSaveResponse,
    DeleteRunResponse,
    DifyConnectionConfigDeleteResponse,
    DifyConnectionConfigListResponse,
    DifyConnectionConfigRequest,
    DifyConnectionConfigItem,
    GenerateDatasetRequest,
    GenerateDatasetResponse,
    HealthResponse,
    KnowledgeBaseListResponse,
    LangSmithExperimentRequest,
    LangSmithExperimentResponse,
    LangSmithSyncRequest,
    LangSmithSyncResponse,
    RenameRunRequest,
    RenameRunResponse,
    ReportResponse,
    RunDetailResponse,
    RunListResponse,
    UpdateRunLabelsRequest,
    UpdateRunLabelsResponse,
)
from backend.error_codes import DEFAULT_HTTP_STATUS, ErrorCode, http_status_for
from backend.services.artifact_store import ARTIFACT_FILES, ArtifactStore, ArtifactStoreError
from backend.services.dataset_edit_service import (
    DatasetEditError,
    delete_dataset,
    export_dataset,
    load_dataset_rows,
    resolve_editable_path,
    save_dataset_rows,
)
from backend.services.dataset_generation_service import DatasetGenerationService
from backend.services.dataset_review_service import (
    commit_review as commit_review_rows,
    draft_path_for,
    read_review_state,
)
from backend.services.db_store import DBStore
from backend.services.langsmith_service import LangSmithService
from backend.services.report_store import now_iso
from backend.services.run_service import RunService, RunServiceError
from backend.services.runner_supervisor import RunnerSupervisor
from backend.services.runner_watchdog import run_watchdog
from backend.db.session import (
    get_session_factory,
    init_db,
    require_alembic_head,
)
from kb_eval.logging_config import (
    configure_logging,
    reset_request_id,
    set_request_id,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_ROOT = PROJECT_ROOT / "reports"
DATASET_ROOTS = [PROJECT_ROOT / "datasets", PROJECT_ROOT.parent / "docs"]
_log = logging.getLogger("backend.app")


# Backwards-compatible alias: existing call sites import ``ALLOWED_ARTIFACTS``
# and ``ReportStoreError`` from ``report_store`` for type checks and the
# artifact route's MIME map. The new code paths live in
# ``backend.services.artifact_store`` and ``backend.services.db_store``.
ALLOWED_ARTIFACTS = ARTIFACT_FILES
ReportStoreError = ArtifactStoreError


# ``ArtifactStoreError`` / ``ReportStoreError`` are bare ``RuntimeError`` without
# a ``code`` field. Translate their free-form ``str(exc)`` messages into the
# unified code catalogue so the frontend sees the same envelope as everywhere
# else. ``_RUNNING_DELETE_PREFIX`` catches the "运行正在进行中…" message that
# appears when a delete lands on a still-running run (409 Conflict).
_STORE_ERROR_MAP: dict[str, tuple[ErrorCode, int]] = {
    "Run not found": (ErrorCode.RUN_NOT_FOUND, 404),
    "Report not found": (ErrorCode.REPORT_NOT_FOUND, 404),
    "Artifact not found": (ErrorCode.ARTIFACT_NOT_FOUND, 404),
    "Invalid run_id": (ErrorCode.RUN_NOT_FOUND, 400),
    "Invalid run path": (ErrorCode.RUN_NOT_FOUND, 400),
    # Artifact path validation failures are caller errors (wrong run_id / name)
    # rather than "missing" — keep them on 400 with a stable code.
    "Artifact not allowed": (ErrorCode.ARTIFACT_NOT_FOUND, 400),
    "Invalid artifact path": (ErrorCode.ARTIFACT_NOT_FOUND, 400),
}
_RUNNING_DELETE_PREFIX = "运行正在进行中"


def _resolve_store_error(exc: Exception) -> tuple[ErrorCode, int, str]:
    """Map a legacy store exception to ``(code, http_status, message)``."""
    msg = str(exc)
    if msg.startswith(_RUNNING_DELETE_PREFIX):
        return ErrorCode.RUN_DELETE_FAILED, 409, msg
    if msg in _STORE_ERROR_MAP:
        code, status = _STORE_ERROR_MAP[msg]
        return code, status, msg
    # Unknown message — keep the raw text in ``detail`` so we can still
    # diagnose it, but mark the code as RUN_DELETE_FAILED so clients can
    # switch on a stable identifier.
    return ErrorCode.RUN_DELETE_FAILED, DEFAULT_HTTP_STATUS, msg


def _coerce_code(code: str) -> ErrorCode:
    """Convert a possibly-legacy string ``code`` into an ``ErrorCode``.

    Falls back to ``ErrorCode.VALIDATION_ERROR`` for unknown strings — that
    way a typo in a raise site shows up as a generic error rather than a 500.
    """
    try:
        return ErrorCode(code)
    except ValueError:
        return ErrorCode.VALIDATION_ERROR


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Load .env (if present) so DATABASE_URL is set before the engine
    # is constructed.
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass
    log_dir = configure_logging(app_name="backend", project_root=PROJECT_ROOT, force=True)
    _log.info("backend startup begin log_dir=%s", log_dir or "(disabled)")
    try:
        if _bootstrap_enabled():
            # Dev path: ``Base.metadata.create_all`` keeps the schema
            # in sync with the models. New tables show up
            # automatically; existing ones don't get new columns --
            # that's the known limitation we're fixing via alembic.
            init_db()
        else:
            # Production path: refuse to start if alembic hasn't
            # brought the DB up to head. ``create_all`` would mask a
            # missing column because it only emits ``CREATE TABLE``,
            # never ``ALTER TABLE``, so the first read on the missing
            # column would 500 the request. Failing at startup is the
            # only way to surface the drift before a user hits it.
            head = require_alembic_head()
            _log.info(
                "alembic head %s confirmed; skipping create_all "
                "(RUN_DB_BOOTSTRAP=false)",
                head,
            )
    except Exception as exc:
        # ``init_db`` / ``require_alembic_head`` already print a
        # one-screen hint to stderr via ``_log_db_unreachable`` for
        # OperationalError and via their own ``print(...)`` for the
        # alembic-mismatch case. For anything else (import error,
        # metadata misconfig, etc.) print a short marker so the user
        # sees the cause before SQLAlchemy's 30-frame dump scrolls
        # past.
        _log.exception("lifespan startup failed: %s: %s", type(exc).__name__, exc)
        raise

    # Spawn the runner subprocess (commit 4) and start the watchdog
    # background task. The supervisor is a no-op when
    # ``EVAL_RUNNER_SUBPROCESS=disabled`` is set, which is the
    # one-env-var rollback to the pre-commit-4 inline behaviour.
    supervisor = RunnerSupervisor(
        database_url=os.environ.get("DATABASE_URL", ""),
        reports_root=REPORTS_ROOT,
        concurrency=int(os.environ.get("EVAL_RUNNER_CONCURRENCY", "8")),
        tick_ms=int(os.environ.get("EVAL_RUNNER_TICK_MS", "500")),
    )
    supervisor.start()
    shutdown_event = asyncio.Event()
    watchdog_task = asyncio.create_task(
        run_watchdog(
            get_session_factory(),
            shutdown=shutdown_event,
        ),
        name="kb-eval-runner-watchdog",
    )

    try:
        _log.info("backend startup complete")
        yield
    finally:
        _log.info("backend shutdown begin")
        # Stop the watchdog, then the subprocess. Reverse order so
        # the subprocess doesn't see a "subprocess disappeared" log
        # line while we're tearing down the supervisor.
        shutdown_event.set()
        try:
            await asyncio.wait_for(watchdog_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            watchdog_task.cancel()
        supervisor.stop(timeout_seconds=5.0)
        _log.info("backend shutdown complete")


def _bootstrap_enabled() -> bool:
    """Mirror :func:`backend.db.session.init_db`'s env-var policy so the
    lifespan decision lives in one place. Default is True (dev-friendly);
    production deployments must set ``RUN_DB_BOOTSTRAP=false`` and own
    the schema lifecycle via ``alembic upgrade head``."""

    import os

    return os.environ.get("RUN_DB_BOOTSTRAP", "true").lower() in {"1", "true", "yes"}


app = FastAPI(title="Dify KB Eval Backend", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8200", "http://localhost:8200", "http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

artifact_store = ArtifactStore(REPORTS_ROOT)
store = DBStore(artifact_store=artifact_store, session_factory=get_session_factory())
run_service = RunService(PROJECT_ROOT, store)
dataset_generation_service = DatasetGenerationService(PROJECT_ROOT)
langsmith_service = LangSmithService(PROJECT_ROOT)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    token = set_request_id(request_id)
    started = time.perf_counter()
    try:
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            _log.exception(
                "request failed method=%s path=%s client=%s duration_ms=%.1f",
                request.method,
                request.url.path,
                request.client.host if request.client else "-",
                duration_ms,
            )
            raise
        duration_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        _log.info(
            "request complete method=%s path=%s status=%s client=%s duration_ms=%.1f",
            request.method,
            request.url.path,
            response.status_code,
            request.client.host if request.client else "-",
            duration_ms,
        )
        return response
    finally:
        reset_request_id(token)


def error_response(code: ErrorCode | str, message: str, status_code: int = 400, detail: dict | None = None) -> JSONResponse:
    """Build the canonical ``{code, message, detail}`` JSON error envelope.

    ``code`` may be an ``ErrorCode`` member or a legacy string — we coerce to
    ``str`` here so the wire format stays a plain string for clients.
    """
    return JSONResponse(
        status_code=status_code,
        content={"code": str(code), "message": message, "detail": detail or {}},
    )


def _error_from_run_exception(
    exc: RunServiceError,
    *,
    default_status: int | None = None,
) -> JSONResponse:
    """Render a ``RunServiceError`` (or subclass) through the unified envelope."""
    return error_response(
        _coerce_code(exc.code),
        exc.message,
        status_code=http_status_for(
            exc.code,
            override=getattr(exc, "http_status", None) or default_status,
        ),
        detail=exc.detail,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(
        ErrorCode.VALIDATION_ERROR,
        "请求参数校验失败",
        status_code=http_status_for(ErrorCode.VALIDATION_ERROR),
        detail={"errors": exc.errors()},
    )


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@app.get("/api/datasets", response_model=DatasetListResponse)
async def list_datasets() -> DatasetListResponse:
    return DatasetListResponse(items=run_service.list_datasets())


@app.get("/api/knowledge-bases", response_model=KnowledgeBaseListResponse)
async def list_knowledge_bases(
    dify_base_url: str = Query(..., description="Dify API base URL"),
    dify_api_key: str = Query(default="", description="Dify Knowledge Base API key"),
    keyword: str | None = Query(default=None, max_length=200, description="Client-side filter"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> KnowledgeBaseListResponse:
    """列出 Dify 中可用的知识库。

    透传到 Dify 知识库列表接口，按 keyword 在前端关心的字段上
    做一次轻量过滤，方便"选 KB"下拉里直接搜。
    """
    try:
        data = run_service.list_knowledge_bases(
            dify_base_url=dify_base_url,
            dify_api_key=dify_api_key,
            keyword=keyword,
            limit=limit,
            offset=offset,
        )
    except RunServiceError as exc:
        # 502 表示"Dify 服务不可达/报错"，区别于请求参数 4xx。
        return _error_from_run_exception(exc, default_status=502)
    return KnowledgeBaseListResponse(**data)


@app.get("/api/dify-connections", response_model=DifyConnectionConfigListResponse)
async def list_dify_connection_configs(
    limit: int = Query(default=20, ge=1, le=100),
) -> DifyConnectionConfigListResponse:
    items = store.list_dify_connection_configs(limit=limit)
    return DifyConnectionConfigListResponse(
        items=[DifyConnectionConfigItem(**item) for item in items],
        total=len(items),
    )


@app.post("/api/dify-connections", response_model=DifyConnectionConfigItem)
async def save_dify_connection_config(
    request: DifyConnectionConfigRequest,
) -> DifyConnectionConfigItem | JSONResponse:
    dify_base_url = request.dify_base_url.strip()
    dify_api_key = request.dify_api_key.strip()
    if not dify_base_url or not dify_api_key:
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            "Dify API 地址和 API Key 都不能为空",
            status_code=http_status_for(ErrorCode.VALIDATION_ERROR),
            detail={"fields": ["dify_base_url", "dify_api_key"]},
        )
    item = store.save_dify_connection_config(
        dify_base_url=dify_base_url,
        dify_api_key=dify_api_key,
    )
    return DifyConnectionConfigItem(**item)


@app.delete("/api/dify-connections/{config_id}", response_model=DifyConnectionConfigDeleteResponse)
async def delete_dify_connection_config(config_id: str) -> DifyConnectionConfigDeleteResponse | JSONResponse:
    cleaned_id = (config_id or "").strip()
    if not cleaned_id:
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            "缺少连接配置 id",
            status_code=http_status_for(ErrorCode.VALIDATION_ERROR),
            detail={"field": "config_id"},
        )
    deleted = store.delete_dify_connection_config(config_id=cleaned_id)
    if not deleted:
        return error_response(
            ErrorCode.DIFY_CONNECTION_CONFIG_NOT_FOUND,
            "未找到该连接配置",
            status_code=http_status_for(ErrorCode.DIFY_CONNECTION_CONFIG_NOT_FOUND),
            detail={"config_id": cleaned_id},
        )
    return DifyConnectionConfigDeleteResponse(id=cleaned_id, deleted=True)


@app.post("/api/datasets/generate", response_model=GenerateDatasetResponse)
async def generate_dataset(request: GenerateDatasetRequest) -> GenerateDatasetResponse:
    try:
        result = dataset_generation_service.generate_dataset(request)
    except RunServiceError as exc:
        return _error_from_run_exception(exc)
    return GenerateDatasetResponse(**result)


@app.post("/api/datasets/generate/upload", response_model=GenerateDatasetResponse)
async def generate_dataset_from_upload(
    files: list[UploadFile] = File(...),
    relative_paths: list[str] = Form(default=[]),
    options: str = Form(...),
) -> GenerateDatasetResponse:
    try:
        request = GenerateDatasetRequest.model_validate_json(options)
    except ValidationError as exc:
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            "生成参数校验失败",
            status_code=http_status_for(ErrorCode.VALIDATION_ERROR),
            detail={"errors": json.loads(exc.json())},
        )

    if len(relative_paths) != len(files):
        relative_paths = [file.filename or "" for file in files]

    try:
        inferred_vendor, inferred_model = dataset_generation_service.infer_vendor_model_from_relative_paths(relative_paths)
        vendor = request.vendor.strip() or inferred_vendor
        model = request.model.strip() or inferred_model
        if not vendor or not model:
            raise RunServiceError(
                ErrorCode.VENDOR_MODEL_REQUIRED,
                "无法从所选目录解析厂商和型号，请选择包含 厂商/型号 层级的目录",
                {"relative_paths": relative_paths[:20]},
            )

        source_dir = dataset_generation_service.uploaded_source_directory(vendor, model)
        saved_files: list[str] = []
        for upload, relative_path in zip(files, relative_paths, strict=True):
            filename = upload.filename or Path(relative_path).name
            target = dataset_generation_service.uploaded_file_path(source_dir, relative_path, filename)
            with target.open("wb") as output:
                while chunk := await upload.read(1024 * 1024):
                    output.write(chunk)
            saved_files.append(dataset_generation_service.display_path(target))
            await upload.close()

        generation_request = request.model_copy(
            update={
                "source_directory": str(source_dir),
                "source_files": [],
                "vendor": vendor,
                "model": model,
            },
        )
        result = dataset_generation_service.generate_dataset(generation_request)
        result["uploaded_files"] = saved_files
    except RunServiceError as exc:
        return _error_from_run_exception(exc)
    return GenerateDatasetResponse(**result)


@app.post("/api/runs", response_model=CreateRunResponse)
async def create_run(
    request: CreateRunRequest,
    background_tasks: BackgroundTasks,
) -> CreateRunResponse:
    """Create a run and let the runner subprocess claim it.

    Commit 4 changed this: the run is created with
    ``status='queued'`` and the supervisor's subprocess claims it
    within ``EVAL_RUNNER_TICK_MS`` (default 500 ms). The request
    returns immediately, the HTTP thread is freed, and 8-way
    concurrent retrieval happens in the subprocess's own event loop.

    Rollback (``EVAL_RUNNER_SUBPROCESS=disabled``)
    ----------------------------------------------
    If the supervisor is disabled, ``BackgroundTasks.add_task`` is
    used instead so the run executes inline in the FastAPI thread
    pool (pre-commit-4 behaviour). The endpoint contract is
    unchanged either way — the response body always reports
    ``status='queued'`` for a freshly created run.
    """

    try:
        manifest, config = run_service.create_run(request)
    except RunServiceError as exc:
        return _error_from_run_exception(exc)

    # Run stays in ``queued``; the runner subprocess claims it
    # when the supervisor is enabled. The ``BackgroundTasks`` path
    # is only used when the supervisor is disabled (pre-commit-4
    # inline mode). We check at request time so a ``kill -HUP`` or
    # env-var flip is honoured without a server restart.
    subprocess_disabled = os.environ.get("EVAL_RUNNER_SUBPROCESS", "enabled").lower() in {
        "disabled",
        "off",
        "false",
        "0",
        "no",
    }
    requires_runtime_secret = bool(request.dify_api_key.strip())
    if requires_runtime_secret:
        manifest = store.update_manifest(
            manifest["id"],
            status="running",
            started_at=now_iso(),
            error="",
        )
    if subprocess_disabled or requires_runtime_secret:
        background_tasks.add_task(
            run_service.execute_run_inline, manifest["id"], config
        )

    return CreateRunResponse(
        id=manifest["id"],
        status=manifest["status"],
        created_at=manifest["created_at"],
        links={"detail": f"/api/runs/{manifest['id']}"},
    )


@app.get("/api/runs", response_model=RunListResponse)
async def list_runs(
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    dify_base_url: str | None = Query(
        default=None,
        description="可选：按 Dify API 地址过滤，空串/未传则不过滤",
    ),
) -> RunListResponse:
    items, total = run_service.list_runs(
        status=status,
        limit=limit,
        offset=offset,
        dify_base_url=dify_base_url,
    )
    return RunListResponse(
        total=total,
        items=[
            {
                "id": item.get("id", ""),
                "name": item.get("name", ""),
                "status": item.get("status", "failed"),
                "created_at": item.get("created_at", ""),
                "finished_at": item.get("finished_at"),
                "duration_ms": item.get("duration_ms"),
                "eval_file": item.get("eval_file", ""),
                "dataset_id": item.get("dataset_id", ""),
                "top_k": item.get("top_k", 5),
                "sample_count": item.get("sample_count", 0),
                "query_count": item.get("query_count", 0),
                "metrics": item.get("metrics") or {},
                "langsmith_url": item.get("langsmith_url"),
                "embedding_model": item.get("embedding_model"),
                "rerank_model": item.get("rerank_model"),
                "dify_base_url": item.get("dify_base_url"),
            }
            for item in items
        ],
    )


@app.get("/api/runs/compare", response_model=CompareRunResponse)
async def compare_runs(
    dataset_id: str = Query(..., min_length=1, description="按 dataset_id 分组对比；必填"),
    top_k: int | None = Query(default=None, ge=1, le=20),
    dify_base_url: str | None = Query(
        default=None,
        description="可选：按 Dify API 地址隔离，空串/未传则不过滤",
    ),
) -> CompareRunResponse:
    """同一 dataset 下按 (embedding, rerank, sample_count) 分组的对比视图。

    - 仅取 ``status == "completed"`` 且 ``deleted_at IS NULL`` 的 run
    - ``top_k`` 为 None 时返回该 dataset 下所有 top_k 的 run
    - ``dify_base_url`` 非空时只返回该 Dify 下的 run（不传 = 全 Dify）
    - 不存在的 dataset 返回空 groups（不 404，避免前端错误处理复杂度）
    - 路由声明必须在 ``GET /api/runs/{run_id}`` 之前，避免 ``compare`` 被
      当成 run_id 路径参数匹配走
    """

    try:
        data = run_service.compare_runs(
            dataset_id=dataset_id,
            top_k=top_k,
            dify_base_url=dify_base_url,
        )
    except RunServiceError as exc:
        return _error_from_run_exception(exc)
    return CompareRunResponse(**data)


@app.get("/api/runs/{run_id}/events")
async def stream_run_events(run_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events stream of run progress.

    Replaces the 2-second ``GET /api/runs/{run_id}`` poll the
    frontend used before commit 5. The browser opens one
    long-lived connection here and receives push notifications as
    the runner writes ``progress`` / ``status`` updates.

    Event types (see ``backend/services/run_event_stream.py``):
        * ``snapshot`` — once on connect, full current state.
        * ``progress`` — emitted on every DB progress diff.
        * ``status`` — once, when the run reaches a terminal
          status; stream closes after.
        * ``ping`` — every 15 s; defeats proxy idle timeouts.
        * ``error`` — once, on unrecoverable error.

    ``Last-Event-ID`` resume header is honoured via the
    in-memory replay buffer; a server restart loses the buffer
    (the next snapshot is the current state, so no progress is
    lost).
    """

    from backend.services.run_event_stream import stream_run_events
    from backend.services.artifact_store import ArtifactStoreError

    try:
        return await stream_run_events(
            run_id, store, request=request
        )
    except ArtifactStoreError as exc:
        # ``build_detail`` raises if the run is missing / soft-deleted.
        # Surface as 404 so the client can show "run gone" instead of
        # an indefinite empty stream.
        code, status, msg = _resolve_store_error(exc)
        return error_response(
            code,
            msg,
            status_code=status,
            detail={"run_id": run_id},
        )


@app.get("/api/runs/{run_id}", response_model=RunDetailResponse)
async def get_run(run_id: str) -> RunDetailResponse:
    try:
        return RunDetailResponse(**run_service.get_detail(run_id))
    except RunServiceError as exc:
        return _error_from_run_exception(exc)
    except ReportStoreError as exc:
        # ``run_service.get_detail`` converts ``ReportStoreError`` into a
        # ``RunServiceError``, but the underlying store can also raise a raw
        # ``ArtifactStoreError`` directly (e.g. ``db_store.build_detail``).
        # Map it here so a missing run returns 404 instead of a generic 500.
        code, status, msg = _resolve_store_error(exc)
        return error_response(
            code,
            msg,
            status_code=status,
            detail={"run_id": run_id},
        )


@app.patch("/api/runs/{run_id}", response_model=RenameRunResponse)
async def rename_run(run_id: str, request: RenameRunRequest) -> RenameRunResponse:
    """改一次历史评测的显示名。

    仅允许修改 ``name``，其它字段（status / 指标 / 产物等）一律走自己的
    路由，避免 PATCH 被误用成万能更新接口。空名走 422，请求了不存在的
    run_id 走 404，便于前端直接根据 status 区分提示。
    """

    try:
        manifest = run_service.rename_run(run_id, request.name)
    except RunServiceError as exc:
        return _error_from_run_exception(exc)
    return RenameRunResponse(
        id=manifest["id"],
        name=manifest["name"],
        updated_at=manifest.get("updated_at"),
    )


@app.post("/api/runs/{run_id}/labels", response_model=UpdateRunLabelsResponse)
async def update_run_labels(run_id: str, request: UpdateRunLabelsRequest) -> UpdateRunLabelsResponse:
    """修改对比分析用的模型标签（embedding / rerank 模型名）。

    走专用 POST 端点而不是 PATCH /runs/{id}，避免 PATCH 演变成"万能
    更新接口"。两个字段都允许显式传 ``null``/空串，store 层统一归
    一为 NULL，请求了不存在的 ``run_id`` 走 404。
    """

    try:
        manifest = run_service.update_run_labels(
            run_id,
            embedding_model=request.embedding_model,
            rerank_model=request.rerank_model,
        )
    except RunServiceError as exc:
        return _error_from_run_exception(exc)
    return UpdateRunLabelsResponse(
        id=manifest["id"],
        embedding_model=manifest.get("embedding_model"),
        rerank_model=manifest.get("rerank_model"),
        updated_at=manifest.get("updated_at"),
    )


@app.get("/api/runs/{run_id}/report", response_model=ReportResponse)
async def get_report(run_id: str) -> ReportResponse:
    try:
        return ReportResponse(run_id=run_id, content=run_service.get_report(run_id))
    except RunServiceError as exc:
        return _error_from_run_exception(exc)


@app.get("/api/runs/{run_id}/artifacts/{name}")
async def get_artifact(run_id: str, name: str) -> FileResponse:
    try:
        path = store.artifact_path(run_id, name)
    except ReportStoreError as exc:
        code, status, msg = _resolve_store_error(exc)
        return error_response(
            code,
            msg,
            status_code=status,
            detail={"run_id": run_id, "name": name},
        )
    return FileResponse(path, media_type=ALLOWED_ARTIFACTS[name], filename=name)


@app.delete("/api/runs/{run_id}", response_model=DeleteRunResponse)
def delete_run_route(run_id: str) -> DeleteRunResponse:
    """Delete a run. Behaviour by status:

    * ``completed`` / ``failed`` / ``canceled``: back up the
      artifact directory, set ``deleted_at``, remove the dir.
    * ``running`` / ``queued``: commit 4 changed this — instead
      of refusing (the pre-commit-4 behaviour), we transition
      the row to ``status='canceled'`` and leave the artifact
      directory in place. The runner subprocess observes the
      cancel on its next coalesced progress flush and aborts the
      in-flight evaluation. A follow-up DELETE then cleans up
      the directory.
    * Missing / already-soft-deleted: idempotent 200 with
      ``status='missing'``.

    The two-step delete (cancel first, hard-delete later) avoids
    racing the runner that might still be writing into
    ``reports/<id>/``.
    """

    try:
        result = store.delete_run(run_id)
    except ReportStoreError as exc:
        code, status, msg = _resolve_store_error(exc)
        return error_response(
            code,
            msg,
            status_code=status,
            detail={"run_id": run_id},
        )
    return DeleteRunResponse(**result)


@app.post("/api/langsmith/datasets/sync", response_model=LangSmithSyncResponse)
async def sync_langsmith_dataset(request: LangSmithSyncRequest) -> LangSmithSyncResponse:
    try:
        eval_file = run_service.resolve_eval_file(request.eval_file)
        result = langsmith_service.sync_dataset(
            eval_file=eval_file,
            dataset_name=request.dataset_name,
            description=request.description,
        )
    except RunServiceError as exc:
        return _error_from_run_exception(exc)
    return LangSmithSyncResponse(**result)


@app.post("/api/langsmith/experiments/run", response_model=LangSmithExperimentResponse)
async def run_langsmith_experiment(
    request: LangSmithExperimentRequest,
    background_tasks: BackgroundTasks,
) -> LangSmithExperimentResponse:
    request.langsmith_enabled = True
    try:
        manifest, config = run_service.create_run(request)
    except RunServiceError as exc:
        return _error_from_run_exception(exc)
    background_tasks.add_task(run_service.execute_run, manifest["id"], config)
    return LangSmithExperimentResponse(
        run_id=manifest["id"],
        experiment_name=manifest["name"],
        langsmith_url=None,
        status="queued",
    )


def _allowed_roots() -> list[Path]:
    return [root.resolve() for root in DATASET_ROOTS if root.exists()]


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    # 白名单目录也作为回退基准：方便测试场景（patched DATASET_ROOTS）
    # 与把数据集存放在非 PROJECT_ROOT 树下的部署形态。
    bases: list[Path] = [PROJECT_ROOT.resolve(), PROJECT_ROOT.parent.resolve()]
    bases.extend(root.resolve() for root in DATASET_ROOTS if root.exists())
    for base in bases:
        try:
            return str(resolved.relative_to(base)).replace("\\", "/")
        except ValueError:
            continue
    return str(resolved)


@app.get("/api/dataset-rows/{eval_file:path}", response_model=DatasetRowsResponse)
async def get_dataset_rows(eval_file: str) -> DatasetRowsResponse:
    try:
        path = resolve_editable_path(eval_file, _allowed_roots())
    except DatasetEditError as exc:
        return _error_from_run_exception(exc)
    review = read_review_state(path)
    # 存在草稿时，编辑器展示草稿内容；main 仍放在 draft_rows 字段以备恢复。
    draft = draft_path_for(path)
    main_payload = None
    if path.exists():
        try:
            main_payload = load_dataset_rows(path)
        except DatasetEditError as exc:
            return _error_from_run_exception(exc)
    active_payload: dict
    if review["status"] == "draft" and draft.exists():
        try:
            active_payload = load_dataset_rows(draft)
        except DatasetEditError as exc:
            return _error_from_run_exception(exc)
    elif main_payload is not None:
        active_payload = main_payload
    else:
        return error_response(
            ErrorCode.DATASET_NOT_FOUND,
            "评测集文件不存在",
            status_code=http_status_for(ErrorCode.DATASET_NOT_FOUND),
            detail={"eval_file": eval_file},
        )
    return DatasetRowsResponse(
        **{
            **active_payload,
            "path": _display_path(path),
            "review_status": review["status"],
            "draft_path": (
                _display_path(draft) if review["status"] == "draft" else None
            ),
            "reviewed_at": review.get("reviewed_at"),
            "reviewed_by": review.get("reviewed_by"),
            "generated_at": review.get("generated_at"),
            "draft_rows": main_payload["rows"] if main_payload else None,
        }
    )


@app.put("/api/dataset-rows/{eval_file:path}", response_model=DatasetSaveResponse)
async def put_dataset_rows(
    eval_file: str,
    request: DatasetSaveRequest,
    target: str = Query("main", pattern="^(main|draft)$"),
) -> DatasetSaveResponse:
    try:
        path = resolve_editable_path(eval_file, _allowed_roots())
    except DatasetEditError as exc:
        return _error_from_run_exception(exc)

    if target == "draft":
        # 编辑的是草稿：写回到 <stem>.draft.jsonl，并保证 review meta 至少为 draft
        from backend.services.dataset_review_service import write_draft
        try:
            write_draft(path, request.rows)
        except DatasetEditError as exc:
            return _error_from_run_exception(exc)
        return DatasetSaveResponse(
            path=_display_path(draft_path_for(path)),
            sample_count=len(request.rows),
            backup_path="",
            saved_at="",
            validation_errors=[],
            target="draft",
        )

    try:
        result = save_dataset_rows(path, request.rows)
    except DatasetEditError as exc:
        return _error_from_run_exception(exc)
    if result["errors"]:
        return JSONResponse(
            status_code=422,
            content=DatasetSaveResponse(
                path=_display_path(path),
                sample_count=0,
                backup_path="",
                saved_at="",
                validation_errors=[
                    DatasetRowValidationError(**error) for error in result["errors"]
                ],
                target="main",
            ).model_dump(),
        )
    return DatasetSaveResponse(
        path=_display_path(path),
        sample_count=len(request.rows),
        backup_path=result["backup_path"],
        saved_at=result["saved_at"],
        validation_errors=[],
        target="main",
    )


@app.post("/api/dataset-rows/{eval_file:path}/review", response_model=DatasetCommitReviewResponse)
async def post_dataset_review(
    eval_file: str, request: DatasetCommitReviewRequest
) -> DatasetCommitReviewResponse:
    try:
        path = resolve_editable_path(eval_file, _allowed_roots())
        result = commit_review_rows(path, request.rows, reviewed_by=request.reviewed_by)
    except DatasetEditError as exc:
        return _error_from_run_exception(exc)
    return DatasetCommitReviewResponse(
        path=_display_path(Path(result["path"])),
        sample_count=result["sample_count"],
        backup_path=result["backup_path"],
        reviewed_at=result["reviewed_at"],
        reviewed_by=result["reviewed_by"],
    )


@app.get("/api/dataset-rows/{eval_file:path}/export", response_model=DatasetExportResponse)
async def get_dataset_export(eval_file: str) -> DatasetExportResponse:
    try:
        path = resolve_editable_path(eval_file, _allowed_roots())
        content = export_dataset(path)
    except DatasetEditError as exc:
        return _error_from_run_exception(exc)
    return DatasetExportResponse(path=_display_path(path), name=path.stem, content=content)


@app.delete("/api/datasets/{eval_file:path}")
async def delete_dataset_route(eval_file: str) -> JSONResponse:
    """删除一个评测集（主 JSONL + 草稿 + 审核元信息）。

    行为：
    - 主 JSONL 会被复制到 ``<name>.deleted-<时间戳>.bak`` 作为一次性备份，
      然后再删除。草稿和审核元信息被一并清理。
    - 路径必须落在 ``_allowed_roots()`` 白名单内，否则返回 400。
    - 不存在的文件返回 404。
    """

    try:
        path = resolve_editable_path(eval_file, _allowed_roots())
        result = delete_dataset(path)
    except DatasetEditError as exc:
        return _error_from_run_exception(exc)
    return JSONResponse(
        status_code=200,
        content={
            "path": _display_path(Path(result["path"])),
            "backup_path": result["backup_path"],
            "removed": result["removed"],
        },
    )

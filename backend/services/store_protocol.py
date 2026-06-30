"""Minimal interface shared by ``ReportStore`` (file-based, kept for tests)
and ``DBStore`` (the production PostgreSQL implementation)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class RunStore(Protocol):
    """The smallest contract that ``RunService`` and the artifact route need.

    The protocol is intentionally narrow: anything not consumed by
    ``backend.services.run_service.RunService`` or the
    ``GET /api/runs/{id}/artifacts/{name}`` route is not part of it.
    """

    def allocate_run_id(self, name: str) -> str: ...

    def create_run(
        self, *, run_id: str, name: str, config: dict[str, Any]
    ) -> dict[str, Any]: ...

    def update_manifest(self, run_id: str, **changes: Any) -> dict[str, Any]: ...

    def update_progress(self, run_id: str, progress: dict[str, Any]) -> None: ...

    def append_log(self, run_id: str, line: str) -> None: ...

    def list_runs(
        self, *, status: str | None = None, limit: int = 20, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]: ...

    def compare_runs(
        self, *, dataset_id: str, top_k: int | None = None
    ) -> dict[str, Any]: ...

    def build_detail(self, run_id: str) -> dict[str, Any]: ...

    def get_report(self, run_id: str) -> str: ...

    def read_summary(self, run_id: str) -> dict[str, Any]: ...

    def read_results(self, run_id: str) -> list[dict[str, Any]]: ...

    def run_dir(self, run_id: str) -> Path: ...

    def artifact_path(self, run_id: str, name: str) -> Path: ...

    def delete_run(self, run_id: str) -> dict[str, Any]: ...

    def rename_run(self, run_id: str, name: str) -> dict[str, Any]: ...

    def update_run_labels(
        self,
        run_id: str,
        *,
        embedding_model: str | None,
        rerank_model: str | None,
    ) -> dict[str, Any]: ...

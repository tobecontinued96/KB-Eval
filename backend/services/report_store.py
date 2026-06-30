"""Local file persistence for evaluation runs."""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path
from typing import Any

from kb_eval.metrics import build_summary
from kb_eval.report import failed_samples


ALLOWED_ARTIFACTS = {
    "manifest.json": "application/json",
    "summary.json": "application/json",
    "results.jsonl": "application/x-ndjson",
    "results.csv": "text/csv; charset=utf-8",
    "report.md": "text/markdown; charset=utf-8",
    "console.log": "text/plain; charset=utf-8",
}


class ReportStoreError(RuntimeError):
    pass


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def duration_ms(started_at: str | None, finished_at: str | None) -> int | None:
    started = parse_iso(started_at)
    finished = parse_iso(finished_at)
    if not started or not finished:
        return None
    return int((finished - started).total_seconds() * 1000)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", value.strip()).strip("-").lower()
    return slug or "kb-eval"


def safe_json_load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


class ReportStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def allocate_run_id(self, name: str) -> str:
        prefix = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        base = f"{prefix}-{slugify(name)}"
        run_id = base
        index = 2
        while (self.root / run_id).exists():
            run_id = f"{base}-{index}"
            index += 1
        return run_id

    def run_dir(self, run_id: str) -> Path:
        if not re.fullmatch(r"[\w\-\u4e00-\u9fff]+", run_id):
            raise ReportStoreError("Invalid run_id")
        path = (self.root / run_id).resolve()
        if self.root.resolve() not in path.parents and path != self.root.resolve():
            raise ReportStoreError("Invalid run path")
        return path

    def create_run(self, *, run_id: str, name: str, config: dict[str, Any]) -> dict[str, Any]:
        run_dir = self.run_dir(run_id)
        run_dir.mkdir(parents=False, exist_ok=False)
        (run_dir / "console.log").write_text("", encoding="utf-8")
        created_at = now_iso()
        manifest = {
            "id": run_id,
            "name": name,
            "status": "queued",
            "created_at": created_at,
            "started_at": None,
            "finished_at": None,
            "duration_ms": None,
            "dify_base_url": config.get("dify_base_url", ""),
            "dataset_id": config.get("dataset_id", ""),
            "eval_file": config.get("eval_file", ""),
            "top_k": config.get("top_k", 5),
            "include_alternatives": config.get("include_alternatives", False),
            "limit": config.get("limit", 0),
            "sample_ids": config.get("sample_ids", []),
            "sample_count": 0,
            "query_count": 0,
            "progress": {
                "total_queries": 0,
                "completed_queries": 0,
                "error_queries": 0,
                "current_sample_id": None,
                "latest_error": None,
            },
            "metrics": {},
            "artifacts": {
                "manifest": "manifest.json",
                "summary": "summary.json",
                "results_jsonl": "results.jsonl",
                "results_csv": "results.csv",
                "report_md": "report.md",
                "console_log": "console.log",
            },
            "langsmith_url": None,
            "error": "",
        }
        self.write_manifest(run_id, manifest)
        return manifest

    def read_manifest(self, run_id: str) -> dict[str, Any]:
        manifest = safe_json_load(self.run_dir(run_id) / "manifest.json", None)
        if not isinstance(manifest, dict):
            raise ReportStoreError("Run not found")
        return manifest

    def write_manifest(self, run_id: str, manifest: dict[str, Any]) -> None:
        path = self.run_dir(run_id) / "manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def update_manifest(self, run_id: str, **changes: Any) -> dict[str, Any]:
        manifest = self.read_manifest(run_id)
        manifest.update(changes)
        if manifest.get("started_at") and manifest.get("finished_at"):
            manifest["duration_ms"] = duration_ms(manifest.get("started_at"), manifest.get("finished_at"))
        self.write_manifest(run_id, manifest)
        return manifest

    def update_progress(self, run_id: str, progress: dict[str, Any]) -> None:
        manifest = self.read_manifest(run_id)
        manifest["progress"] = progress
        self.write_manifest(run_id, manifest)

    def append_log(self, run_id: str, line: str) -> None:
        with (self.run_dir(run_id) / "console.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{now_iso()} {line}\n")

    def list_runs(self, *, status: str | None = None, limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        manifests: list[dict[str, Any]] = []
        for child in self.root.iterdir():
            if not child.is_dir():
                continue
            # 跳过 delete_run 留的整目录备份（<run_id>.deleted-<UTC 时间戳>），
            # 它们是误删回滚用的副本，不应该出现在历史评测列表里。
            if child.name.endswith(".deleted") or ".deleted-" in child.name:
                continue
            manifest = safe_json_load(child / "manifest.json", None)
            if not isinstance(manifest, dict):
                continue
            if status and manifest.get("status") != status:
                continue
            manifests.append(manifest)
        manifests.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        manifests = [self.enrich_manifest_metrics(item) for item in manifests]
        total = len(manifests)
        return manifests[offset: offset + limit], total

    def read_summary(self, run_id: str) -> dict[str, Any]:
        summary = safe_json_load(self.run_dir(run_id) / "summary.json", {})
        if not isinstance(summary, dict):
            summary = {}
        summary.setdefault("overall", {})
        summary.setdefault("by_scenario_type", {})
        return summary

    def read_results(self, run_id: str) -> list[dict[str, Any]]:
        path = self.run_dir(run_id) / "results.jsonl"
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    row = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        return rows

    def with_content_hits(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized_rows: list[dict[str, Any]] = []
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
                (index for index, hit in enumerate(hits, start=1) if hit),
                None,
            )
            normalized_rows.append(copied)
        return normalized_rows

    def ensure_content_metrics(self, summary: dict[str, Any], rows: list[dict[str, Any]], top_k: int) -> dict[str, Any]:
        overall = summary.get("overall")
        if not rows or not isinstance(overall, dict) or "content_recall@5" in overall:
            return summary
        normalized_rows = self.with_content_hits(rows)
        return build_summary(normalized_rows, top_k=top_k)

    def retrieval_samples(self, rows: list[dict[str, Any]], *, limit: int = 20) -> list[dict[str, Any]]:
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
                    },
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
                },
            )
        return items

    def enrich_manifest_metrics(self, manifest: dict[str, Any]) -> dict[str, Any]:
        metrics = manifest.get("metrics")
        if isinstance(metrics, dict) and "content_recall@5" in metrics:
            return manifest
        run_id = str(manifest.get("id") or "")
        if not run_id:
            return manifest
        try:
            top_k = int(manifest.get("top_k") or 5)
            summary = self.ensure_content_metrics(self.read_summary(run_id), self.read_results(run_id), top_k)
        except ReportStoreError:
            return manifest
        overall = summary.get("overall")
        if isinstance(overall, dict) and overall:
            enriched = dict(manifest)
            enriched["metrics"] = overall
            return enriched
        return manifest

    def build_detail(self, run_id: str) -> dict[str, Any]:
        manifest = self.read_manifest(run_id)
        rows = self.with_content_hits(self.read_results(run_id))
        top_k = int(manifest.get("top_k") or 5)
        summary = self.ensure_content_metrics(self.read_summary(run_id), rows, top_k)
        artifacts = [
            {
                "name": name,
                "type": artifact_type(name),
                "url": f"/api/runs/{run_id}/artifacts/{name}",
            }
            for name in ALLOWED_ARTIFACTS
            if (self.run_dir(run_id) / name).exists()
        ]
        return {
            "id": manifest.get("id", run_id),
            "name": manifest.get("name", ""),
            "status": manifest.get("status", "failed"),
            "created_at": manifest.get("created_at", ""),
            "started_at": manifest.get("started_at"),
            "finished_at": manifest.get("finished_at"),
            "duration_ms": manifest.get("duration_ms"),
            "eval_file": manifest.get("eval_file", ""),
            "dataset_id": manifest.get("dataset_id", ""),
            "top_k": top_k,
            "sample_count": manifest.get("sample_count", 0),
            "query_count": manifest.get("query_count", 0),
            "metrics": summary.get("overall") or manifest.get("metrics") or {},
            "progress": manifest.get("progress") or {},
            "config": {
                "dify_base_url": manifest.get("dify_base_url", ""),
                "dataset_id": manifest.get("dataset_id", ""),
                "eval_file": manifest.get("eval_file", ""),
                "top_k": top_k,
                "include_alternatives": manifest.get("include_alternatives", False),
                "limit": manifest.get("limit", 0),
                "sample_ids": manifest.get("sample_ids", []),
            },
            "summary": summary,
            "failed_samples": failed_samples(rows, top_k=top_k),
            "retrieval_samples": self.retrieval_samples(rows),
            "artifacts": artifacts,
            "langsmith_url": manifest.get("langsmith_url"),
            "error": manifest.get("error", ""),
        }

    def artifact_path(self, run_id: str, name: str) -> Path:
        if name not in ALLOWED_ARTIFACTS:
            raise ReportStoreError("Artifact not allowed")
        path = (self.run_dir(run_id) / name).resolve()
        if self.run_dir(run_id).resolve() not in path.parents:
            raise ReportStoreError("Invalid artifact path")
        if not path.exists():
            raise ReportStoreError("Artifact not found")
        return path

    def get_report(self, run_id: str) -> str:
        try:
            path = self.artifact_path(run_id, "report.md")
        except ReportStoreError as exc:
            raise ReportStoreError("Report not found") from exc
        return path.read_text(encoding="utf-8")

    def delete_run(self, run_id: str) -> dict[str, Any]:
        """删除一次历史评测的整份目录（含所有产物），删除前自动备份。

        与评测集删除语义对齐：
        - 正在跑（``running`` / ``queued``）的运行不能直接删除，避免与后台
          ``run_service.execute_run`` 写文件的逻辑打架。
        - 删除前把整份目录复制到 ``reports/<id>.deleted-<UTC 时间戳>`` 作为
          一次性备份，方便误删后回滚。
        - **目录不存在视为幂等成功**：重复点击删除（例如前端在 2s 轮询窗口
          里的重试，或两个标签页同时删除）不应该再返回错误，让前端能干净地
          回到列表页。
        """

        run_dir = self.run_dir(run_id)
        if not run_dir.exists():
            return {
                "id": run_id,
                "status": "missing",
                "backup_path": None,
            }

        manifest = safe_json_load(run_dir / "manifest.json", {})
        status = str(manifest.get("status") or "") if isinstance(manifest, dict) else ""
        if status in {"running", "queued"}:
            raise ReportStoreError(
                f"运行正在进行中（{status}），请等待结束后再删除。"
            )

        backup_target = self.root / f"{run_id}.deleted-{now_iso().replace(':', '').replace('+', '-')}"
        counter = 1
        while backup_target.exists():
            backup_target = self.root / f"{run_id}.deleted-{counter}"
            counter += 1
        shutil.copytree(run_dir, backup_target)
        shutil.rmtree(run_dir)
        return {
            "id": run_id,
            "status": status,
            "backup_path": str(backup_target),
        }


def artifact_type(name: str) -> str:
    if name.endswith(".md"):
        return "markdown"
    if name.endswith(".json"):
        return "json"
    if name.endswith(".jsonl"):
        return "jsonl"
    if name.endswith(".csv"):
        return "csv"
    return "text"

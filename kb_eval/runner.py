"""Evaluation runner shared by CLI, backend, and future LangSmith integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from kb_eval.dataset import filter_samples, load_samples, query_variants
from kb_eval.dify_client import DifyClient
from kb_eval.errors import EvalError
from kb_eval.metrics import build_summary, evaluate_query
from kb_eval.progress_coalescer import ProgressCoalescer
from kb_eval.report import write_csv, write_json, write_jsonl, write_report


ProgressCallback = Callable[[dict[str, Any]], None]
LogCallback = Callable[[str], None]


@dataclass(frozen=True)
class EvalRunConfig:
    name: str
    dify_base_url: str
    dataset_id: str
    eval_file: Path
    top_k: int = 5
    include_alternatives: bool = False
    limit: int = 0
    sample_ids: list[str] = field(default_factory=list)
    timeout_seconds: float = 60
    dify_api_key: str = ""
    langsmith_enabled: bool = False
    langsmith_project: str = "dify-kb-eval"
    # 仅作对比分析标签：embedding / rerank 模型名。runner 不参与检索逻辑，
    # 仅在 public_dict / manifest 里透传给 store 落库。
    embedding_model: str = ""
    rerank_model: str = ""

    def validate(self) -> None:
        if not self.dify_base_url.strip():
            raise EvalError("dify_base_url is required")
        if not self.dify_api_key.strip():
            raise EvalError("Dify API key is required for direct connection")
        if not 1 <= self.top_k <= 20:
            raise EvalError("top_k must be between 1 and 20")
        if self.limit < 0:
            raise EvalError("limit cannot be negative")
        if self.timeout_seconds <= 0:
            raise EvalError("timeout_seconds must be positive")

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dify_base_url": self.dify_base_url,
            "dataset_id": self.dataset_id,
            "eval_file": str(self.eval_file),
            "top_k": self.top_k,
            "include_alternatives": self.include_alternatives,
            "limit": self.limit,
            "sample_ids": self.sample_ids,
            "timeout_seconds": self.timeout_seconds,
            "langsmith_enabled": self.langsmith_enabled,
            "langsmith_project": self.langsmith_project,
            "embedding_model": self.embedding_model,
            "rerank_model": self.rerank_model,
        }


def run_evaluation(
    config: EvalRunConfig,
    output_dir: Path,
    *,
    on_progress: ProgressCallback | None = None,
    on_log: LogCallback | None = None,
) -> dict[str, Any]:
    config.validate()
    output_dir.mkdir(parents=True, exist_ok=True)

    def log(message: str) -> None:
        if on_log:
            on_log(message)

    samples = filter_samples(load_samples(config.eval_file), config.sample_ids, config.limit)
    if not samples:
        raise EvalError("No samples to evaluate")

    vendors = sorted({sample.vendor for sample in samples})
    models = sorted({sample.model for sample in samples})
    planned_queries = sum(
        1 + (len(sample.alternative_queries) if config.include_alternatives else 0)
        for sample in samples
    )
    log(f"Loaded {len(samples)} samples from {config.eval_file}")
    log(f"Vendors: {', '.join(vendors)}")
    log(f"Models: {', '.join(models)}")
    log(f"Planned queries: {planned_queries}")

    client = DifyClient(
        config.dify_base_url,
        token=config.dify_api_key,
        timeout=config.timeout_seconds,
    )
    dataset_info: dict[str, Any] | None = None
    if config.dataset_id:
        dataset_id = config.dataset_id
        log(f"Using provided dataset id: {dataset_id}")
        dataset_info = client.validate_dataset_name(
            dataset_id=dataset_id,
            vendor=samples[0].vendor,
            model=samples[0].model,
            on_warning=log,
        )
        if dataset_info:
            log(
                "Validated knowledge base name: "
                f"{dataset_info.get('name') or dataset_info.get('display_name') or ''}",
            )
    else:
        vendor = samples[0].vendor
        model = samples[0].model
        if len(vendors) > 1 or len(models) > 1:
            log("Multiple vendors/models detected; auto resolution uses the first sample.")
        dataset_id, dataset_info, candidates = client.resolve_dataset_id(vendor=vendor, model=model)
        log(f"Resolved dataset id: {dataset_id}")
        for item in candidates:
            log(
                "Candidate: "
                f"{item.get('dataset_id') or item.get('id')} | "
                f"{item.get('vendor') or ''}/{item.get('model') or ''} | {item.get('name') or ''}",
            )

    rows: list[dict[str, Any]] = []
    completed = 0
    errors = 0
    # Throttle the per-query progress callback so we don't slam the DB
    # with one short transaction per (sample, query). The coalescer
    # writes at most once per ``min_interval_ms`` (default 200ms) and
    # at least once per ``max_interval_ms`` (default 1000ms); the
    # ``flush()`` at the end guarantees the final ``completed/total``
    # always lands even if the last update was within the min window.
    coalescer = (
        ProgressCoalescer(sink=on_progress) if on_progress else None
    )
    initial_snapshot = {
        "total_queries": planned_queries,
        "completed_queries": 0,
        "error_queries": 0,
        "current_sample_id": None,
    }
    if coalescer is not None:
        coalescer.update(initial_snapshot)

    for sample in samples:
        for query_kind, query in query_variants(sample, config.include_alternatives):
            completed += 1
            log(f"[{completed}/{planned_queries}] {sample.id} {query_kind}: {query}")
            try:
                chunks, latency_ms = client.retrieve(dataset_id=dataset_id, query=query, top_k=config.top_k)
                rows.append(
                    evaluate_query(
                        sample=sample,
                        query_kind=query_kind,
                        query=query,
                        chunks=chunks,
                        latency_ms=latency_ms,
                        dataset_id=dataset_id,
                        top_k=config.top_k,
                    ),
                )
            except EvalError as exc:
                errors += 1
                rows.append(
                    evaluate_query(
                        sample=sample,
                        query_kind=query_kind,
                        query=query,
                        chunks=[],
                        latency_ms=0,
                        dataset_id=dataset_id,
                        top_k=config.top_k,
                        error_message=str(exc),
                    ),
                )
            if coalescer is not None:
                coalescer.update(
                    {
                        "total_queries": planned_queries,
                        "completed_queries": completed,
                        "error_queries": errors,
                        "current_sample_id": sample.id if completed < planned_queries else None,
                    },
                )

    if coalescer is not None:
        # Force-emit the final snapshot so the UI / DB never sees a
        # value that's behind by one query. This is the only place we
        # can guarantee the very last ``completed`` lands — the inner
        # loop's ``update`` may have been dropped by the min_interval
        # floor.
        coalescer.flush()

    summary = build_summary(rows, top_k=config.top_k)
    write_jsonl(output_dir / "results.jsonl", rows)
    write_json(output_dir / "summary.json", summary)
    write_csv(output_dir / "results.csv", rows)
    write_report(
        output_dir / "report.md",
        config=config.public_dict(),
        dataset_id=dataset_id,
        dataset_info=dataset_info,
        summary=summary,
        rows=rows,
    )
    log(f"Report: {output_dir / 'report.md'}")
    return {
        "dataset_id": dataset_id,
        "dataset_info": dataset_info,
        "sample_count": len(samples),
        "query_count": planned_queries,
        "rows": rows,
        "summary": summary,
    }

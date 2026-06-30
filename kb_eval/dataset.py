"""Dataset loading and validation for retrieval evaluation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kb_eval.errors import EvalError


@dataclass(frozen=True)
class EvalSample:
    id: str
    vendor: str
    model: str
    scenario_type: str
    topic: str
    question: str
    alternative_queries: list[str]
    expected_documents: list[str]
    expected_sections: list[str]
    expected_keywords: list[str]
    evaluation_focus: str
    raw: dict[str, Any]


REQUIRED_FIELDS = [
    "id",
    "vendor",
    "model",
    "scenario_type",
    "topic",
    "question",
    "expected_documents",
    "expected_sections",
    "expected_keywords",
    "evaluation_focus",
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise EvalError(f"Eval file not found: {path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise EvalError(f"Invalid JSON at {path}:{line_no}: {exc}") from exc
            if not isinstance(row, dict):
                raise EvalError(f"{path}:{line_no}: JSONL row must be an object")
            rows.append(row)
    return rows


def require_list(value: Any, field: str, sample_id: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise EvalError(f"{sample_id}: field {field} must be a list of strings")
    return value


def load_samples(path: Path) -> list[EvalSample]:
    rows = read_jsonl(path)
    samples: list[EvalSample] = []
    for index, row in enumerate(rows, start=1):
        sample_id = str(row.get("id") or f"row-{index}")
        for field in REQUIRED_FIELDS:
            if not row.get(field):
                raise EvalError(f"{sample_id}: missing required field {field}")
        samples.append(
            EvalSample(
                id=sample_id,
                vendor=str(row["vendor"]).strip(),
                model=str(row["model"]).strip(),
                scenario_type=str(row["scenario_type"]).strip(),
                topic=str(row["topic"]).strip(),
                question=str(row["question"]).strip(),
                alternative_queries=require_list(row.get("alternative_queries"), "alternative_queries", sample_id),
                expected_documents=require_list(row.get("expected_documents"), "expected_documents", sample_id),
                expected_sections=require_list(row.get("expected_sections"), "expected_sections", sample_id),
                expected_keywords=require_list(row.get("expected_keywords"), "expected_keywords", sample_id),
                evaluation_focus=str(row["evaluation_focus"]).strip(),
                raw=row,
            ),
        )

    ids = [sample.id for sample in samples]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise EvalError(f"Duplicate sample ids: {', '.join(duplicates)}")
    return samples


def filter_samples(samples: list[EvalSample], sample_ids: list[str], limit: int) -> list[EvalSample]:
    if sample_ids:
        wanted = {item.strip() for item in sample_ids if item.strip()}
        samples = [sample for sample in samples if sample.id in wanted]
        missing = sorted(wanted - {sample.id for sample in samples})
        if missing:
            raise EvalError(f"Requested sample ids not found: {', '.join(missing)}")
    if limit:
        samples = samples[:limit]
    return samples


def query_variants(sample: EvalSample, include_alternatives: bool) -> list[tuple[str, str]]:
    variants = [("primary", sample.question)]
    if include_alternatives:
        variants.extend((f"alt-{index}", query) for index, query in enumerate(sample.alternative_queries, start=1))
    return variants


def dataset_metadata(path: Path) -> dict[str, Any]:
    samples = load_samples(path)
    vendors = sorted({sample.vendor for sample in samples})
    models = sorted({sample.model for sample in samples})
    scenario_types = sorted({sample.scenario_type for sample in samples})
    scenario_distribution: dict[str, int] = {}
    for sample in samples:
        scenario_distribution[sample.scenario_type] = scenario_distribution.get(sample.scenario_type, 0) + 1
    updated_at = None
    try:
        updated_at = path.stat().st_mtime
    except OSError:
        updated_at = None
    return {
        "sample_count": len(samples),
        "vendor": vendors[0] if len(vendors) == 1 else "",
        "model": models[0] if len(models) == 1 else "",
        "vendors": vendors,
        "models": models,
        "scenario_types": scenario_types,
        "scenario_distribution": scenario_distribution,
        "updated_at_epoch": updated_at,
    }

"""Retrieval matching and metric calculation."""

from __future__ import annotations

import math
import re
import statistics
from typing import Any

from kb_eval.dataset import EvalSample
from kb_eval.retrieval_utils import text_contains


def section_aliases(section: str) -> list[str]:
    aliases = [section]
    title = re.sub(r"^#+\s*", "", section).strip()
    title = re.sub(r"^\d+(\.\d+)*\s*", "", title).strip()
    if title and title not in aliases:
        aliases.append(title)
    for part in re.split(r"\s*/\s*", section):
        part = part.strip()
        if part and part not in aliases:
            aliases.append(part)
            stripped = re.sub(r"^\d+(\.\d+)*\s*", "", part).strip()
            if stripped and stripped not in aliases:
                aliases.append(stripped)
    return aliases


def doc_matches(expected_documents: list[str], chunk: dict[str, Any]) -> bool:
    source = chunk.get("source")
    candidates = [
        chunk.get("document_name"),
        source.get("document_name") if isinstance(source, dict) else "",
        chunk.get("document_url"),
        chunk.get("content"),
    ]
    for expected in expected_documents:
        if any(text_contains(candidate, expected) for candidate in candidates):
            return True
    return False


def section_matches(expected_sections: list[str], chunk: dict[str, Any]) -> bool:
    source = chunk.get("source")
    text = " ".join(
        str(part or "")
        for part in [
            chunk.get("content"),
            chunk.get("document_name"),
            source.get("document_name") if isinstance(source, dict) else "",
        ]
    )
    return any(text_contains(text, alias) for section in expected_sections for alias in section_aliases(section))


def keyword_matches(expected_keywords: list[str], chunk: dict[str, Any]) -> list[str]:
    text = " ".join(
        str(part or "")
        for part in [
            chunk.get("content"),
            chunk.get("document_name"),
            " ".join(map(str, chunk.get("keywords") or [])),
        ]
    )
    return [keyword for keyword in expected_keywords if text_contains(text, keyword)]


def enough_keywords(expected_keywords: list[str], matches: list[str]) -> bool:
    if not expected_keywords:
        return False
    threshold = 1 if len(expected_keywords) <= 2 else 2
    return len(set(matches)) >= threshold


def rank_or_none(values: list[bool]) -> int | None:
    for index, value in enumerate(values, start=1):
        if value:
            return index
    return None


def evaluate_query(
    *,
    sample: EvalSample,
    query_kind: str,
    query: str,
    chunks: list[dict[str, Any]],
    latency_ms: float,
    dataset_id: str,
    top_k: int,
    error_message: str = "",
) -> dict[str, Any]:
    result_rows: list[dict[str, Any]] = []
    doc_hits: list[bool] = []
    section_hits: list[bool] = []
    keyword_hits: list[bool] = []
    content_hits: list[bool] = []

    for rank, chunk in enumerate(chunks[:top_k], start=1):
        doc_hit = doc_matches(sample.expected_documents, chunk)
        section_hit = section_matches(sample.expected_sections, chunk)
        keyword_match_list = keyword_matches(sample.expected_keywords, chunk)
        keyword_hit = enough_keywords(sample.expected_keywords, keyword_match_list)
        content_hit = doc_hit or section_hit or keyword_hit
        doc_hits.append(doc_hit)
        section_hits.append(section_hit)
        keyword_hits.append(keyword_hit)
        content_hits.append(content_hit)
        content = str(chunk.get("content") or "")
        result_rows.append(
            {
                "rank": rank,
                "document_id": chunk.get("document_id") or "",
                "document_name": chunk.get("document_name") or "",
                "score": chunk.get("score", 0),
                "doc_hit": doc_hit,
                "section_hit": section_hit,
                "keyword_hit": keyword_hit,
                "content_hit": content_hit,
                "keyword_matches": keyword_match_list,
                "content_preview": re.sub(r"\s+", " ", content).strip()[:240],
            },
        )

    return {
        "sample_id": sample.id,
        "vendor": sample.vendor,
        "model": sample.model,
        "scenario_type": sample.scenario_type,
        "topic": sample.topic,
        "query_kind": query_kind,
        "query": query,
        "dataset_id": dataset_id,
        "top_k": top_k,
        "latency_ms": round(latency_ms, 2),
        "error": error_message,
        "result_count": len(chunks),
        "expected_documents": sample.expected_documents,
        "expected_sections": sample.expected_sections,
        "expected_keywords": sample.expected_keywords,
        "doc_hit_rank": rank_or_none(doc_hits),
        "section_hit_rank": rank_or_none(section_hits),
        "keyword_hit_rank": rank_or_none(keyword_hits),
        "content_hit_rank": rank_or_none(content_hits),
        "top_results": result_rows,
    }


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = math.ceil((p / 100) * len(ordered)) - 1
    index = min(max(index, 0), len(ordered) - 1)
    return ordered[index]


def hit_at(rank: int | None, k: int) -> bool:
    return rank is not None and rank <= k


HIT_RANK_FIELDS = {
    "content_hit": "content_hit_rank",
    "doc_hit": "doc_hit_rank",
    "section_hit": "section_hit_rank",
    "keyword_hit": "keyword_hit_rank",
}


def result_hit(item: dict[str, Any], hit_field: str) -> bool:
    if hit_field == "content_hit":
        return bool(
            item.get("content_hit")
            or item.get("doc_hit")
            or item.get("section_hit")
            or item.get("keyword_hit")
        )
    return bool(item.get(hit_field))


def hits_at(row: dict[str, Any], hit_field: str, k: int) -> list[bool]:
    top_results = row.get("top_results") or []
    hits = [
        result_hit(item, hit_field)
        for item in top_results[:k]
        if isinstance(item, dict)
    ]
    if not hits:
        rank = row.get(HIT_RANK_FIELDS.get(hit_field, ""))
        hits = [rank == index for index in range(1, k + 1)]
    if len(hits) < k:
        hits.extend([False] * (k - len(hits)))
    return hits[:k]


def precision_at(row: dict[str, Any], hit_field: str, k: int) -> float:
    if k <= 0:
        return 0.0
    return sum(hits_at(row, hit_field, k)) / k


def dcg_at(hits: list[bool]) -> float:
    return sum(1 / math.log2(rank + 1) for rank, hit in enumerate(hits, start=1) if hit)


def ndcg_at(row: dict[str, Any], hit_field: str, k: int) -> float:
    hits = hits_at(row, hit_field, k)
    ideal = dcg_at(sorted(hits, reverse=True))
    if ideal == 0:
        return 0.0
    return dcg_at(hits) / ideal


def metric_block(rows: list[dict[str, Any]], ks: list[int]) -> dict[str, Any]:
    total = len(rows)
    if total == 0:
        block: dict[str, Any] = {
            "total_queries": 0,
            "completed_queries": 0,
            "error_queries": 0,
            "empty_result_rate": 0,
            "avg_latency_ms": 0,
            "p95_latency_ms": 0,
            "document_mrr": 0,
            "content_mrr": 0,
            "section_mrr": 0,
            "keyword_mrr": 0,
        }
        for k in ks:
            for prefix in ["document", "content", "section", "keyword"]:
                block[f"{prefix}_recall@{k}"] = 0
                block[f"{prefix}_precision@{k}"] = 0
                block[f"{prefix}_ndcg@{k}"] = 0
        return block
    latencies = [float(row["latency_ms"]) for row in rows if not row.get("error")]
    block: dict[str, Any] = {
        "total_queries": total,
        "completed_queries": sum(1 for row in rows if not row.get("error")),
        "error_queries": sum(1 for row in rows if row.get("error")),
        "empty_result_rate": round(sum(1 for row in rows if row.get("result_count") == 0) / total, 4),
        "avg_latency_ms": round(statistics.mean(latencies), 2) if latencies else 0,
        "p95_latency_ms": round(percentile(latencies, 95), 2) if latencies else 0,
        "document_mrr": round(
            sum((1 / row["doc_hit_rank"]) if row.get("doc_hit_rank") else 0 for row in rows) / total,
            4,
        ),
        "content_mrr": round(
            sum((1 / row["content_hit_rank"]) if row.get("content_hit_rank") else 0 for row in rows) / total,
            4,
        ),
        "section_mrr": round(
            sum((1 / row["section_hit_rank"]) if row.get("section_hit_rank") else 0 for row in rows) / total,
            4,
        ),
        "keyword_mrr": round(
            sum((1 / row["keyword_hit_rank"]) if row.get("keyword_hit_rank") else 0 for row in rows) / total,
            4,
        ),
    }
    for k in ks:
        block[f"document_recall@{k}"] = round(sum(hit_at(row.get("doc_hit_rank"), k) for row in rows) / total, 4)
        block[f"content_recall@{k}"] = round(sum(hit_at(row.get("content_hit_rank"), k) for row in rows) / total, 4)
        block[f"section_recall@{k}"] = round(sum(hit_at(row.get("section_hit_rank"), k) for row in rows) / total, 4)
        block[f"keyword_recall@{k}"] = round(sum(hit_at(row.get("keyword_hit_rank"), k) for row in rows) / total, 4)
        block[f"document_precision@{k}"] = round(sum(precision_at(row, "doc_hit", k) for row in rows) / total, 4)
        block[f"content_precision@{k}"] = round(sum(precision_at(row, "content_hit", k) for row in rows) / total, 4)
        block[f"section_precision@{k}"] = round(sum(precision_at(row, "section_hit", k) for row in rows) / total, 4)
        block[f"keyword_precision@{k}"] = round(sum(precision_at(row, "keyword_hit", k) for row in rows) / total, 4)
        block[f"document_ndcg@{k}"] = round(sum(ndcg_at(row, "doc_hit", k) for row in rows) / total, 4)
        block[f"content_ndcg@{k}"] = round(sum(ndcg_at(row, "content_hit", k) for row in rows) / total, 4)
        block[f"section_ndcg@{k}"] = round(sum(ndcg_at(row, "section_hit", k) for row in rows) / total, 4)
        block[f"keyword_ndcg@{k}"] = round(sum(ndcg_at(row, "keyword_hit", k) for row in rows) / total, 4)
    return block


def build_summary(rows: list[dict[str, Any]], *, top_k: int) -> dict[str, Any]:
    ks = sorted({1, 3, 5, top_k})
    ks = [k for k in ks if k <= top_k]
    by_scenario: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_scenario.setdefault(row["scenario_type"], []).append(row)
    return {
        "top_k": top_k,
        "ks": ks,
        "overall": metric_block(rows, ks),
        "by_scenario_type": {
            scenario: metric_block(scenario_rows, ks)
            for scenario, scenario_rows in sorted(by_scenario.items())
        },
    }

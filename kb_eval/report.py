"""Artifact writers for evaluation runs."""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any

from kb_eval.metrics import hit_at


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "sample_id",
        "query_kind",
        "scenario_type",
        "topic",
        "query",
        "result_count",
        "doc_hit_rank",
        "content_hit_rank",
        "section_hit_rank",
        "keyword_hit_rank",
        "latency_ms",
        "error",
        "top1_document",
        "top1_score",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            top1 = row.get("top_results", [{}])[0] if row.get("top_results") else {}
            writer.writerow(
                {
                    **{field: row.get(field, "") for field in fields},
                    "top1_document": top1.get("document_name", ""),
                    "top1_score": top1.get("score", ""),
                },
            )


def fmt_rate(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def markdown_metrics_table(summary: dict[str, Any]) -> str:
    ks = summary["ks"]
    headers = [
        "范围",
        "样本数",
        "错误",
        "空结果",
        "平均耗时(ms)",
        "P95耗时(ms)",
        "Content MRR",
    ]
    headers.extend(f"Content Recall@{k}" for k in ks)
    headers.extend(f"Content Precision@{k}" for k in ks)
    headers.extend(f"Content NDCG@{k}" for k in ks)
    headers.extend(f"Doc Recall@{k}" for k in ks)
    headers.extend(f"Sec Recall@{k}" for k in ks)
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]

    def row(name: str, block: dict[str, Any]) -> list[str]:
        values = [
            name,
            str(block.get("total_queries", 0)),
            str(block.get("error_queries", 0)),
            fmt_rate(block.get("empty_result_rate", 0)),
            f"{float(block.get('avg_latency_ms', 0)):.2f}",
            f"{float(block.get('p95_latency_ms', 0)):.2f}",
            f"{float(block.get('content_mrr', 0)):.3f}",
        ]
        values.extend(fmt_rate(block.get(f"content_recall@{k}", 0)) for k in ks)
        values.extend(fmt_rate(block.get(f"content_precision@{k}", 0)) for k in ks)
        values.extend(f"{float(block.get(f'content_ndcg@{k}', 0)):.3f}" for k in ks)
        values.extend(fmt_rate(block.get(f"document_recall@{k}", 0)) for k in ks)
        values.extend(fmt_rate(block.get(f"section_recall@{k}", 0)) for k in ks)
        return values

    lines.append("| " + " | ".join(row("整体", summary["overall"])) + " |")
    for scenario, block in summary["by_scenario_type"].items():
        lines.append("| " + " | ".join(row(scenario, block)) + " |")
    return "\n".join(lines)


def write_report(
    path: Path,
    *,
    config: dict[str, Any],
    dataset_id: str,
    dataset_info: dict[str, Any] | None,
    summary: dict[str, Any],
    rows: list[dict[str, Any]],
    langsmith_url: str | None = None,
) -> None:
    top_k = int(config.get("top_k") or 5)
    failures = [
        row for row in rows
        if row.get("error") or not hit_at(row.get("content_hit_rank"), min(top_k, 5))
    ][:20]
    lines = [
        "# 知识库检索评测报告",
        "",
        f"- 生成时间：{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 评测文件：`{config.get('eval_file', '')}`",
        f"- API Base URL：`{config.get('dify_base_url', '')}`",
        f"- Dataset ID：`{dataset_id}`",
        f"- Top K：`{top_k}`",
        f"- 同义问法：`{'开启' if config.get('include_alternatives') else '关闭'}`",
    ]
    if langsmith_url:
        lines.append(f"- LangSmith：{langsmith_url}")
    if dataset_info:
        lines.extend(
            [
                f"- 知识库名称：`{dataset_info.get('name') or dataset_info.get('display_name') or ''}`",
                f"- 厂商 / 型号：`{dataset_info.get('vendor') or ''}` / `{dataset_info.get('model') or ''}`",
            ],
        )
    lines.extend(["", "## 指标总览", "", markdown_metrics_table(summary), ""])
    lines.extend(
        [
            "## 失败样本 Top 20",
            "",
            "| ID | 主题 | Query | 错误 | Top1 文档 | 期望文档 |",
            "|---|---|---|---|---|---|",
        ],
    )
    if failures:
        for row in failures:
            top1 = row.get("top_results", [{}])[0] if row.get("top_results") else {}
            lines.append(
                "| {id} | {topic} | {query} | {error} | {top1} | {expected} |".format(
                    id=row.get("sample_id", ""),
                    topic=str(row.get("topic", "")).replace("|", "\\|"),
                    query=str(row.get("query", "")).replace("|", "\\|")[:80],
                    error=str(row.get("error", "")).replace("|", "\\|")[:80],
                    top1=str(top1.get("document_name", "")).replace("|", "\\|")[:80],
                    expected=", ".join(row.get("expected_documents") or []).replace("|", "\\|")[:120],
                ),
            )
    else:
        lines.append("| - | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## 产物说明",
            "",
            "- `results.jsonl`：逐 query 详细结果，包含 Top K 分段、命中位次、耗时和错误。",
            "- `summary.json`：整体和按场景类型聚合的指标。",
            "- `results.csv`：便于 Excel 打开的扁平结果。",
        ],
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def failed_samples(rows: list[dict[str, Any]], *, top_k: int, limit: int = 20) -> list[dict[str, Any]]:
    failures = [
        row for row in rows
        if row.get("error") or not hit_at(row.get("content_hit_rank"), min(top_k, 5))
    ][:limit]
    items: list[dict[str, Any]] = []
    for row in failures:
        top1 = row.get("top_results", [{}])[0] if row.get("top_results") else {}
        items.append(
            {
                "sample_id": row.get("sample_id", ""),
                "topic": row.get("topic", ""),
                "query": row.get("query", ""),
                "doc_hit_rank": row.get("doc_hit_rank"),
                "content_hit_rank": row.get("content_hit_rank"),
                "top1_document": top1.get("document_name", ""),
                "expected_documents": row.get("expected_documents", []),
                "error": row.get("error", ""),
            },
        )
    return items

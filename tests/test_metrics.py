from __future__ import annotations

import unittest

from kb_eval.metrics import build_summary, ndcg_at, precision_at
from kb_eval.report import markdown_metrics_table


class RankingMetricTests(unittest.TestCase):
    def test_build_summary_includes_precision_ndcg_and_latency(self) -> None:
        rows = [
            {
                "scenario_type": "配置",
                "latency_ms": 100.0,
                "error": "",
                "result_count": 5,
                "doc_hit_rank": 1,
                "content_hit_rank": 1,
                "section_hit_rank": 3,
                "keyword_hit_rank": None,
                "top_results": [
                    {"doc_hit": True, "section_hit": False, "keyword_hit": False, "content_hit": True},
                    {"doc_hit": False, "section_hit": False, "keyword_hit": False, "content_hit": False},
                    {"doc_hit": False, "section_hit": True, "keyword_hit": False, "content_hit": True},
                    {"doc_hit": False, "section_hit": False, "keyword_hit": False, "content_hit": False},
                    {"doc_hit": False, "section_hit": False, "keyword_hit": False, "content_hit": False},
                ],
            },
            {
                "scenario_type": "配置",
                "latency_ms": 300.0,
                "error": "",
                "result_count": 5,
                "doc_hit_rank": None,
                "content_hit_rank": None,
                "section_hit_rank": None,
                "keyword_hit_rank": None,
                "top_results": [
                    {"doc_hit": False, "section_hit": False, "keyword_hit": False, "content_hit": False},
                    {"doc_hit": False, "section_hit": False, "keyword_hit": False, "content_hit": False},
                    {"doc_hit": False, "section_hit": False, "keyword_hit": False, "content_hit": False},
                    {"doc_hit": False, "section_hit": False, "keyword_hit": False, "content_hit": False},
                    {"doc_hit": False, "section_hit": False, "keyword_hit": False, "content_hit": False},
                ],
            },
        ]

        summary = build_summary(rows, top_k=5)
        overall = summary["overall"]

        self.assertEqual(summary["ks"], [1, 3, 5])
        self.assertEqual(overall["content_recall@3"], 0.5)
        self.assertEqual(overall["content_precision@3"], 0.3333)
        self.assertEqual(overall["content_precision@5"], 0.2)
        self.assertEqual(overall["content_ndcg@3"], 0.4599)
        self.assertEqual(overall["avg_latency_ms"], 200.0)
        self.assertEqual(overall["p95_latency_ms"], 300.0)
        self.assertEqual(summary["by_scenario_type"]["配置"]["content_precision@3"], 0.3333)

    def test_ranking_metrics_fall_back_to_hit_rank_for_legacy_rows(self) -> None:
        row = {
            "content_hit_rank": 2,
            "top_results": [],
        }

        self.assertAlmostEqual(precision_at(row, "content_hit", 3), 1 / 3)
        self.assertAlmostEqual(ndcg_at(row, "content_hit", 3), 0.6309297536)

        row_with_legacy_hit_fields = {
            "top_results": [
                {"doc_hit": False, "section_hit": False, "keyword_hit": False},
                {"doc_hit": False, "section_hit": True, "keyword_hit": False},
            ],
        }
        self.assertAlmostEqual(precision_at(row_with_legacy_hit_fields, "content_hit", 2), 0.5)

    def test_markdown_report_exposes_ranking_and_latency_columns(self) -> None:
        summary = {
            "ks": [5],
            "overall": {
                "total_queries": 1,
                "error_queries": 0,
                "empty_result_rate": 0,
                "avg_latency_ms": 123.45,
                "p95_latency_ms": 200.0,
                "content_mrr": 1,
                "content_recall@5": 1,
                "content_precision@5": 0.4,
                "content_ndcg@5": 0.92,
                "document_recall@5": 1,
                "section_recall@5": 1,
            },
            "by_scenario_type": {},
        }

        table = markdown_metrics_table(summary)

        self.assertIn("平均耗时(ms)", table)
        self.assertIn("P95耗时(ms)", table)
        self.assertIn("Content Precision@5", table)
        self.assertIn("Content NDCG@5", table)
        self.assertIn("40.0%", table)
        self.assertIn("0.920", table)


if __name__ == "__main__":
    unittest.main()

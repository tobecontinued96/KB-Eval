"""Optional LangSmith integration placeholders.

Phase one keeps local reports as the source of truth. These helpers expose a
stable API surface while avoiding a hard runtime dependency on external network
access or credentials.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from kb_eval.dataset import load_samples


class LangSmithService:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    @property
    def enabled(self) -> bool:
        return bool(os.getenv("LANGSMITH_API_KEY"))

    def sync_dataset(self, *, eval_file: Path, dataset_name: str, description: str = "") -> dict[str, Any]:
        samples = load_samples(eval_file)
        if not self.enabled:
            return {
                "dataset_name": dataset_name,
                "example_count": len(samples),
                "langsmith_url": None,
                "status": "disabled",
                "description": description,
            }

        # The concrete LangSmith upload can be added without changing the API.
        return {
            "dataset_name": dataset_name,
            "example_count": len(samples),
            "langsmith_url": None,
            "status": "not_implemented",
            "description": description,
        }


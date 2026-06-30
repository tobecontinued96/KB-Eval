"""On-disk file storage for run artifacts.

After the metadata migration, the on-disk role is narrowed to just the
**streamed** artifacts the user wants to download (``results.jsonl``,
``results.csv``, ``console.log``) and the working files the
``kb_eval.runner`` writes during evaluation (``summary.json``,
``report.md``). Once a run terminates, ``DBStore.persist_run_artifacts``
reads ``summary.json`` and ``report.md`` off disk and copies them into
PostgreSQL; the runner-emitted files are then deleted so the on-disk
directory ends up holding exactly the three downloadable artifacts.
"""

from __future__ import annotations

import json
import re
import shutil
import datetime as dt
from pathlib import Path
from typing import Any


#: Set of artifact names the frontend is allowed to download via
#: ``GET /api/runs/{run_id}/artifacts/{name}``.
ARTIFACT_FILES: dict[str, str] = {
    "results.jsonl": "application/x-ndjson",
    "results.csv": "text/csv; charset=utf-8",
    "console.log": "text/plain; charset=utf-8",
}

#: Files the runner writes to the run directory; we copy them into PG and
#: delete them from disk once the run is done.
RUNNER_EMITTED_FILES: tuple[str, ...] = ("summary.json", "report.md")


class ArtifactStoreError(RuntimeError):
    pass


_RUN_ID_RE = re.compile(r"^[\w\-一-鿿]+$")


def _now_compact() -> str:
    """Return an ISO-8601 local-time timestamp with ``:`` and ``+`` replaced,
    matching the existing ``reports/<id>.deleted-<UTC>`` filename scheme."""

    now = dt.datetime.now(dt.timezone.utc).astimezone()
    return now.isoformat(timespec="seconds").replace(":", "").replace("+", "-")


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- path validation ----

    def run_dir(self, run_id: str) -> Path:
        if not _RUN_ID_RE.fullmatch(run_id):
            raise ArtifactStoreError("Invalid run_id")
        path = (self.root / run_id).resolve()
        if self.root.resolve() not in path.parents and path != self.root.resolve():
            raise ArtifactStoreError("Invalid run path")
        return path

    def ensure_run_dir(self, run_id: str) -> Path:
        path = self.run_dir(run_id)
        path.mkdir(parents=False, exist_ok=False)
        return path

    # ---- console.log ----

    def touch_console_log(self, run_id: str) -> Path:
        path = self.run_dir(run_id) / "console.log"
        path.write_text("", encoding="utf-8")
        return path

    def append_log(self, run_id: str, line: str) -> None:
        path = self.run_dir(run_id) / "console.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{dt.datetime.now().astimezone().isoformat(timespec='seconds')} {line}\n")

    # ---- runner outputs (results.*, summary.json, report.md) ----

    def has_results(self, run_id: str) -> bool:
        return (self.run_dir(run_id) / "results.jsonl").exists()

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

    def read_summary(self, run_id: str) -> dict[str, Any]:
        path = self.run_dir(run_id) / "summary.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def read_report(self, run_id: str) -> str:
        path = self.run_dir(run_id) / "report.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def remove_runner_emit_files(self, run_id: str) -> None:
        """Best-effort: delete summary.json and report.md from the run dir
        after they've been copied into PG. Safe to call multiple times."""
        for name in RUNNER_EMITTED_FILES:
            try:
                (self.run_dir(run_id) / name).unlink(missing_ok=True)
            except OSError:
                pass

    # ---- artifacts for download ----

    def artifact_path(self, run_id: str, name: str) -> Path:
        if name not in ARTIFACT_FILES:
            raise ArtifactStoreError("Artifact not allowed")
        path = (self.run_dir(run_id) / name).resolve()
        if self.run_dir(run_id).resolve() not in path.parents:
            raise ArtifactStoreError("Invalid artifact path")
        if not path.exists():
            raise ArtifactStoreError("Artifact not found")
        return path

    def list_artifacts(self, run_id: str) -> list[str]:
        try:
            run_path = self.run_dir(run_id)
        except ArtifactStoreError:
            return []
        if not run_path.exists():
            return []
        return [name for name in ARTIFACT_FILES if (run_path / name).exists()]

    # ---- backup + remove (used by DBStore.delete_run) ----

    def backup_run_dir(self, run_id: str) -> Path:
        """Copy the run dir to ``reports/<id>.deleted-<UTC>`` and return the
        new path. If a backup with the same timestamp already exists, append
        ``-1``, ``-2`` ... so repeated calls are safe."""

        run_path = self.run_dir(run_id)
        if not run_path.exists():
            raise ArtifactStoreError("Run not found")
        target = self.root / f"{run_id}.deleted-{_now_compact()}"
        counter = 1
        while target.exists():
            target = self.root / f"{run_id}.deleted-{counter}"
            counter += 1
        shutil.copytree(run_path, target)
        return target

    def remove_run_dir(self, run_id: str) -> None:
        run_path = self.run_dir(run_id)
        if run_path.exists():
            shutil.rmtree(run_path)

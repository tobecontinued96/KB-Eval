"""Reusable helpers for the DB-backed run store tests.

The tests run against an in-memory SQLite via the same SQLAlchemy models the
production code uses. Setting ``DIFY_KB_EVAL_TEST_MODE=1`` swaps ``JSONB`` for
``JSON`` so the models are portable to SQLite; everything else is the same
code path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Force-override (not setdefault) so a real DATABASE_URL inherited
# from the user's shell environment does not bleed into the test
# fixtures. The DB layer uses sync ``sqlite3`` for tests.
os.environ["DIFY_KB_EVAL_TEST_MODE"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"


def make_db_store(reports_root: Path) -> Any:
    """Build a real ``DBStore`` against an in-memory SQLite with on-disk
    artifacts rooted at ``reports_root``. Call this from each test's
    ``setUp`` (or directly from an async setup) to ensure a clean engine
    (the engine is module-level cached)."""

    from backend.db.base import Base
    from backend.db.session import get_engine, get_session_factory, reset_for_tests
    from backend.services.artifact_store import ArtifactStore
    from backend.services.db_store import DBStore

    reset_for_tests()
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    sm = get_session_factory()
    artifacts = ArtifactStore(reports_root)
    return DBStore(artifact_store=artifacts, session_factory=sm)

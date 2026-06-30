"""Test package for default unittest discovery."""

from __future__ import annotations

import os

# Pin the test-mode env vars BEFORE any backend import happens during
# ``unittest discover``. ``backend.db.models`` reads ``DIFY_KB_EVAL_TEST_MODE`` to
# swap ``JSONB`` for ``JSON`` so the models work on SQLite in unit tests.
# Force-override (do not use setdefault) so a real ``DATABASE_URL`` from
# the user's shell environment (e.g. ``.env`` loaded by ``start.bat``)
# does not bleed into the unit tests. Tests always use in-memory SQLite.
os.environ["DIFY_KB_EVAL_TEST_MODE"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

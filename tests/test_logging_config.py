"""Tests for the shared logging configuration."""

from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path

from kb_eval.logging_config import (
    configure_logging,
    reset_request_id,
    reset_run_id,
    set_request_id,
    set_run_id,
)


def _close_kb_eval_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_kb_eval_logging", False):
            root.removeHandler(handler)
            handler.close()


class LoggingConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        _close_kb_eval_handlers()
        for key in (
            "LOG_BACKUP_COUNT",
            "LOG_DIR",
            "LOG_LEVEL",
            "LOG_MAX_BYTES",
            "LOG_TO_FILE",
        ):
            os.environ.pop(key, None)

    def test_file_logging_includes_request_and_run_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["LOG_DIR"] = tmp
            os.environ["LOG_LEVEL"] = "INFO"
            log_dir = configure_logging(app_name="unit", project_root=Path(tmp), force=True)

            request_token = set_request_id("req-123")
            run_token = set_run_id("run-456")
            try:
                logging.getLogger("tests.logging").info("hello logging")
            finally:
                reset_run_id(run_token)
                reset_request_id(request_token)

            for handler in logging.getLogger().handlers:
                handler.flush()

            self.assertEqual(log_dir, Path(tmp))
            content = (Path(tmp) / "unit.log").read_text(encoding="utf-8")
            self.assertIn("hello logging", content)
            self.assertIn("request_id=req-123", content)
            self.assertIn("run_id=run-456", content)
            _close_kb_eval_handlers()


if __name__ == "__main__":
    unittest.main()

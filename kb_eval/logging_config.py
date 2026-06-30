"""Central logging setup shared by the backend and evaluation runner."""

from __future__ import annotations

import contextvars
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


_REQUEST_ID = contextvars.ContextVar("kb_eval_request_id", default="-")
_RUN_ID = contextvars.ContextVar("kb_eval_run_id", default="-")


class _ContextFilter(logging.Filter):
    """Inject request/run context into every record our handlers emit."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _REQUEST_ID.get()
        record.run_id = _RUN_ID.get()
        return True


def set_request_id(value: str | None):
    return _REQUEST_ID.set(value or "-")


def reset_request_id(token) -> None:
    _REQUEST_ID.reset(token)


def set_run_id(value: str | None):
    return _RUN_ID.set(value or "-")


def reset_run_id(token) -> None:
    _RUN_ID.reset(token)


def configure_logging(
    *,
    app_name: str = "dify-kb-eval",
    project_root: Path | None = None,
    force: bool = False,
) -> Path | None:
    """Configure console + rotating file logs.

    Environment variables:
      LOG_LEVEL: INFO by default.
      LOG_DIR: defaults to <project_root>/logs.
      LOG_TO_FILE: true by default.
      LOG_MAX_BYTES: defaults to 10485760 (10 MiB).
      LOG_BACKUP_COUNT: defaults to 5.

    Returns the log directory when file logging is enabled, else None.
    """

    root_dir = project_root or Path(__file__).resolve().parents[1]
    level = _resolve_level(os.environ.get("LOG_LEVEL", "INFO"))
    log_to_file = os.environ.get("LOG_TO_FILE", "true").lower() not in {
        "0",
        "false",
        "off",
        "no",
    }
    raw_log_dir = os.environ.get("LOG_DIR")
    log_dir = Path(raw_log_dir) if raw_log_dir else root_dir / "logs"
    if not log_dir.is_absolute():
        log_dir = root_dir / log_dir
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(process)d %(name)s "
        "request_id=%(request_id)s run_id=%(run_id)s %(message)s"
    )
    context_filter = _ContextFilter()

    handlers: list[logging.Handler] = []
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)
    console.addFilter(context_filter)
    console._kb_eval_logging = True  # type: ignore[attr-defined]
    handlers.append(console)

    if log_to_file:
        log_dir.mkdir(parents=True, exist_ok=True)
        max_bytes = _env_int("LOG_MAX_BYTES", 10 * 1024 * 1024)
        backup_count = _env_int("LOG_BACKUP_COUNT", 5)

        app_handler = RotatingFileHandler(
            log_dir / f"{app_name}.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        app_handler.setLevel(level)
        app_handler.setFormatter(formatter)
        app_handler.addFilter(context_filter)
        app_handler._kb_eval_logging = True  # type: ignore[attr-defined]
        handlers.append(app_handler)

        error_handler = RotatingFileHandler(
            log_dir / f"{app_name}.error.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        error_handler.addFilter(context_filter)
        error_handler._kb_eval_logging = True  # type: ignore[attr-defined]
        handlers.append(error_handler)

    root_logger = logging.getLogger()
    if force:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            handler.close()
    else:
        for handler in list(root_logger.handlers):
            if getattr(handler, "_kb_eval_logging", False):
                root_logger.removeHandler(handler)
                handler.close()
    root_logger.setLevel(level)
    for handler in handlers:
        root_logger.addHandler(handler)

    # Route uvicorn's named loggers through the same handlers so access,
    # startup, and application logs land in the same files.
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(level)

    logging.getLogger(__name__).info(
        "logging configured app=%s level=%s log_dir=%s",
        app_name,
        logging.getLevelName(level),
        str(log_dir) if log_to_file else "(disabled)",
    )
    return log_dir if log_to_file else None


def _resolve_level(value: str) -> int:
    level = logging.getLevelName(str(value).upper())
    return level if isinstance(level, int) else logging.INFO


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


__all__ = [
    "configure_logging",
    "reset_request_id",
    "reset_run_id",
    "set_request_id",
    "set_run_id",
]

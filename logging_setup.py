"""
logging_setup.py
Configures file logging for mini-agent. Diagnostics and errors go to a
rotating log file; stdout stays reserved for user-facing output only.

Logs default to <app_dir>/logs/mini-agent.log in development (the empty `logs/`
folder shipped with the project), and to <base_dir>/logs/mini-agent.log when
frozen (the exe dir may be read-only). Override with the `log_file` config key,
and adjust verbosity with `log_level` (DEBUG / INFO / WARNING / ERROR).
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import get_app_dir, get_base_dir

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DEFAULT_MAX_BYTES = 1_000_000  # 1 MB per file
_DEFAULT_BACKUP_COUNT = 3       # keep mini-agent.log.1 .. .3


def get_log_file(config: dict) -> Path:
    """Resolve where the log file lives: config override, or a writable dir.

    In development the log goes next to the app (`<app_dir>/logs/`). When
    frozen with PyInstaller the exe directory is often read-only (e.g. Program
    Files on Windows), so the log goes under the per-user config dir instead
    (`<base_dir>/logs/`), which is always writable.
    """
    override = (config.get("log_file") or "").strip()
    if override:
        return Path(override).expanduser()
    if getattr(sys, "frozen", False):
        return get_base_dir() / "logs" / "mini-agent.log"
    return get_app_dir() / "logs" / "mini-agent.log"


def setup_logging(config: dict) -> Path:
    """Configure the root logger to write to a rotating file. Returns the path."""
    log_file = get_log_file(config)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    level = logging.getLevelName((config.get("log_level") or "INFO").upper())
    if not isinstance(level, int):
        level = logging.INFO

    handler = RotatingFileHandler(
        log_file,
        maxBytes=_DEFAULT_MAX_BYTES,
        backupCount=_DEFAULT_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))

    root = logging.getLogger()
    root.setLevel(level)
    # Idempotent: clear any handlers from a previous call (e.g. in tests).
    for existing in root.handlers[:]:
        root.removeHandler(existing)
        existing.close()
    root.addHandler(handler)

    return log_file

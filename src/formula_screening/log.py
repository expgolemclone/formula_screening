"""Logging configuration."""

import logging
import sys
from logging.handlers import RotatingFileHandler

from formula_screening.config import LOG_DIR, ensure_dirs

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_configured = False

# Default log settings
_LOG_MAX_BYTES = 5_242_880  # 5MB
_LOG_BACKUP_COUNT = 3


def setup_logging(*, verbose: bool = False, quiet: bool = False) -> None:
    """Configure root logger with stderr + rotating file handler.

    Args:
        verbose: Set log level to DEBUG.
        quiet: Set log level to WARNING (overrides verbose).
    """
    global _configured
    if _configured:
        return
    _configured = True

    ensure_dirs()

    if quiet:
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    root = logging.getLogger("formula_screening")
    root.setLevel(level)

    # stderr handler
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(stderr_handler)

    # rotating file handler
    file_handler = RotatingFileHandler(
        LOG_DIR / "screening.log",
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(file_handler)

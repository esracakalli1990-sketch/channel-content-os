"""Logging setup for Channel Content OS.

Call ``setup_logging()`` once at application start (in ``cli.py`` or ``__main__.py``).
All other modules use ``logging.getLogger(__name__)``.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_CONFIGURED = False

LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    root: Path | None = None,
    *,
    level: int = logging.INFO,
    log_to_file: bool = True,
) -> None:
    """Configure the root logger for the application.

    Parameters
    ----------
    root:
        Project root directory. A ``logs/`` subdirectory will be created
        for the file handler. ``None`` disables file logging.
    level:
        Minimum log level.
    log_to_file:
        Whether to write logs to a file in addition to stderr.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # --- Console handler ---
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    root_logger.addHandler(console)

    # --- File handler (optional) ---
    if log_to_file and root is not None:
        log_dir = root / "logs"
        log_dir.mkdir(exist_ok=True)
        from logging.handlers import RotatingFileHandler

        file_handler = RotatingFileHandler(
            log_dir / "channel_ops.log",
            maxBytes=2 * 1024 * 1024,  # 2 MB
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
        root_logger.addHandler(file_handler)

    # Quiet noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

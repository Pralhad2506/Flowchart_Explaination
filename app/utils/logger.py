"""
utils/logger.py — Centralised structured logging for the entire application.

Usage:
    from  utils.logger import get_logger
    logger = get_logger(__name__)
    logger.info("Processing file", extra={"file": "report.pdf"})
"""

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

from  app.config import settings

# ── Formatter ─────────────────────────────────────────────────────────────────

LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _build_formatter() -> logging.Formatter:
    return logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)


# ── Root logger factory ───────────────────────────────────────────────────────

def _configure_root_logger() -> None:
    """Configure the root logger once at import time."""
    root = logging.getLogger()
    if root.handlers:
        return  # Already configured

    root.setLevel(logging.DEBUG)

    # Console handler — INFO and above
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(_build_formatter())
    root.addHandler(ch)

    # Rotating file handler — DEBUG and above (keeps last 10 × 5 MB)
    log_file = settings.log_dir / "diagram_processor.log"
    fh = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=10, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(_build_formatter())
    root.addHandler(fh)


_configure_root_logger()


def get_logger(name: str) -> logging.Logger:
    """Return a named child logger inheriting root handlers."""
    return logging.getLogger(name)
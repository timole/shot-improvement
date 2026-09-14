"""Shared logging setup.

A size-capped rotating file (this machine has little RAM/disk to
spare, so logs must never grow unbounded) plus console output. Only
lifecycle events, warnings and errors are logged at the default INFO
level - deliberately not a per-frame trace, which would add real
overhead on already CPU-constrained hardware for every single one of
the ~7 frames/second this app processes. Set SHOT_IMPROVEMENT_DEBUG=1
to also get per-frame DEBUG detail when actually chasing something
(a hang, a freeze) that needs it.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOG_DIR / "shot-improvement.log"
MAX_BYTES = 2_000_000
BACKUP_COUNT = 2

_configured = False


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if os.environ.get("SHOT_IMPROVEMENT_DEBUG") else logging.INFO

    root = logging.getLogger("shot_improvement")
    root.setLevel(level)

    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(console_handler)

    root.info("Logging started (level=%s, file=%s)", logging.getLevelName(level), LOG_FILE)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"shot_improvement.{name}")

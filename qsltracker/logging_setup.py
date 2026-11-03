"""File logging setup for qsltracker.

The log file lives parallel to the configuration file
(``<config dir>/qsltracker.log``). Call :func:`setup_file_logging` once at
startup; repeat calls for the same file will not add duplicate handlers.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

LOG_FILENAME = "qsltracker.log"


def setup_file_logging(config_path: str | Path) -> Path:
    """Route the ``qsltracker`` logger to ``qsltracker.log`` by the config."""
    log_path = Path(config_path).expanduser().parent / LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("qsltracker")
    logger.setLevel(logging.INFO)
    # File-only: never let log records bubble to the console/TUI.
    logger.propagate = False
    wanted = os.path.abspath(log_path)
    for handler in logger.handlers:
        if (isinstance(handler, logging.FileHandler)
                and os.path.abspath(handler.baseFilename) == wanted):
            return log_path
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(name)s %(levelname)s: %(message)s")
    )
    logger.addHandler(handler)
    return log_path

"""
logger.py — Structured logging with UTF-8 console output + rotating file handler.
Windows-safe: forces UTF-8 stdout so emoji in log messages work correctly.
"""

import io
import logging
import os
import sys
from logging.handlers import RotatingFileHandler


def setup_logger(
    name: str = "torrent_bot",
    log_level: str = "INFO",
    log_file: str = "logs/bot.log",
) -> logging.Logger:
    """Set up and return a configured logger."""
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Avoid duplicate handlers if called multiple times
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Console handler ───────────────────────────────────────────────────────
    # Force UTF-8 on Windows so emoji in log messages don't crash cp1252
    if sys.platform == "win32":
        # Wrap stdout in a UTF-8 writer; reconfigure is available in 3.7+
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
        except AttributeError:
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8", errors="replace"
            )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    # ── Rotating file handler (UTF-8 always) ──────────────────────────────────
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


# Module-level default logger
log = setup_logger()

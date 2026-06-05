"""
config.py — Central configuration loader.
All settings are read from environment variables (via .env).
"""

import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # ── Telegram ──────────────────────────────────────────────────────────────
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    API_ID: int = int(os.getenv("API_ID", "0"))
    API_HASH: str = os.getenv("API_HASH", "")
    STRING_SESSION: str = os.getenv("STRING_SESSION", "")
    _leech_raw: str = os.getenv("LEECH_GROUP_ID", "0")
    LEECH_COMMAND: str = os.getenv("LEECH_COMMAND", "/leech")
    LEECH_GROUP_ID: int | str = int(_leech_raw) if _leech_raw.lstrip("-").isdigit() else _leech_raw
    ADMIN_IDS: List[int] = field(
        default_factory=lambda: [
            int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
        ]
    )

    # ── Scraper ───────────────────────────────────────────────────────────────
    REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "15"))
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
    RESULTS_PER_PAGE: int = int(os.getenv("RESULTS_PER_PAGE", "5"))
    MAX_RESULTS: int = int(os.getenv("MAX_RESULTS", "500"))
    SCRAPER_CONCURRENCY: int = int(os.getenv("SCRAPER_CONCURRENCY", "5"))

    # ── Cache ─────────────────────────────────────────────────────────────────
    CACHE_TTL: int = int(os.getenv("CACHE_TTL", "600"))          # seconds
    CACHE_MAX_SIZE: int = int(os.getenv("CACHE_MAX_SIZE", "200"))
    CACHE_DIR: str = os.getenv("CACHE_DIR", "cache")

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    RATE_LIMIT_CALLS: int = int(os.getenv("RATE_LIMIT_CALLS", "5"))
    RATE_LIMIT_PERIOD: int = int(os.getenv("RATE_LIMIT_PERIOD", "60"))  # seconds

    # ── Database ──────────────────────────────────────────────────────────────
    DB_PATH: str = os.getenv("DB_PATH", "data/torrentbot.db")

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE: str = os.getenv("LOG_FILE", "logs/bot.log")

    # ── Proxy (optional) ──────────────────────────────────────────────────────
    HTTP_PROXY: str = os.getenv("HTTP_PROXY", "")

    # ── Torrent Sites ─────────────────────────────────────────────────────────
    HDHUB4U_URL: str = os.getenv("HDHUB4U_URL", "https://new2.hdhub4u.limo")


# Singleton
settings = Config()

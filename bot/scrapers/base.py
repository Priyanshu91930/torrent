"""
base.py — Abstract base scraper with retry logic, rotating user agents,
          timeout handling, and Cloudflare detection.
"""

import asyncio
import random
from abc import ABC, abstractmethod
from typing import List, Optional

import aiohttp
from fake_useragent import UserAgent

from bot.models import TorrentResult, SearchQuery
from bot.utils.logger import log

# ── User Agent pool ───────────────────────────────────────────────────────────
try:
    _ua = UserAgent()

    def random_ua() -> str:
        return _ua.random
except Exception:
    _FALLBACK_UAS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    ]

    def random_ua() -> str:
        return random.choice(_FALLBACK_UAS)


# ── Abstract Scraper ──────────────────────────────────────────────────────────

class BaseScraper(ABC):
    """
    Abstract base class for all torrent site scrapers.

    Subclasses must implement:
        - name: str
        - search(query, session) -> List[TorrentResult]
        - fetch_latest(session) -> List[TorrentResult]   (optional override)

    Built-in features:
        - Rotating user-agent headers on every request
        - Configurable timeout
        - Automatic retry with exponential backoff
        - Cloudflare challenge detection
        - Graceful error handling
    """

    name: str = "BaseScraper"
    base_url: str = ""

    def __init__(self, timeout: int = 15, max_retries: int = 3, proxy: str = ""):
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_retries = max_retries
        self.proxy = proxy or None

    def _headers(self) -> dict:
        return {
            "User-Agent": random_ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

    @staticmethod
    def _is_cloudflare(html: str) -> bool:
        cf_markers = [
            "Checking if the site connection is secure",
            "cf-browser-verification",
            "cloudflare",
            "Just a moment",
            "Enable JavaScript and cookies",
        ]
        return any(m.lower() in html.lower() for m in cf_markers)

    async def _get(
        self,
        session: aiohttp.ClientSession,
        url: str,
        params: Optional[dict] = None,
    ) -> Optional[str]:
        """
        Perform a GET request with retry + backoff.
        Returns the response text or None on failure.
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                async with session.get(
                    url,
                    params=params,
                    headers=self._headers(),
                    timeout=self.timeout,
                    proxy=self.proxy,
                    allow_redirects=True,
                    ssl=False,
                ) as resp:
                    text = await resp.text(errors="replace")

                    if resp.status == 403 or self._is_cloudflare(text):
                        log.warning(
                            f"[{self.name}] Cloudflare detected at {url}"
                        )
                        return None

                    if resp.status != 200:
                        log.warning(
                            f"[{self.name}] HTTP {resp.status} for {url} "
                            f"(attempt {attempt}/{self.max_retries})"
                        )
                    else:
                        return text

            except asyncio.TimeoutError:
                log.warning(
                    f"[{self.name}] Timeout on {url} (attempt {attempt})"
                )
            except aiohttp.ClientError as e:
                log.warning(
                    f"[{self.name}] Client error on {url}: {e} (attempt {attempt})"
                )
            except Exception as e:
                log.error(f"[{self.name}] Unexpected error: {e}")

            if attempt < self.max_retries:
                wait = 2 ** attempt + random.uniform(0, 1)
                await asyncio.sleep(wait)

        log.error(f"[{self.name}] All {self.max_retries} attempts failed for {url}")
        return None

    @abstractmethod
    async def search(
        self,
        query: SearchQuery,
        session: aiohttp.ClientSession,
    ) -> List[TorrentResult]:
        """Search for torrents matching the query."""
        ...

    async def fetch_latest(
        self, session: aiohttp.ClientSession
    ) -> List[TorrentResult]:
        """Fetch latest uploads. Default: returns empty list."""
        return []

    async def fetch_trending(
        self, session: aiohttp.ClientSession
    ) -> List[TorrentResult]:
        """Fetch trending/top torrents. Default: returns empty list."""
        return []

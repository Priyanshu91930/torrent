"""
manager.py — Orchestrates all scrapers: runs them concurrently,
             aggregates results, deduplicates, and applies filters.
"""

import asyncio
from typing import List, Optional, Callable

import aiohttp

from bot.models import TorrentResult, SearchQuery
from bot.scrapers.base import BaseScraper
from bot.scrapers.hdhub4u import HDHub4uScraper
from bot.utils.dedup import deduplicate
from bot.utils.health import score_results
from bot.utils.logger import log
from bot.config import settings


# ── Available scrapers ────────────────────────────────────────────────────────
ALL_SCRAPERS: List[BaseScraper] = [
    HDHub4uScraper(timeout=settings.REQUEST_TIMEOUT, max_retries=settings.MAX_RETRIES),
]

# Category → which scrapers are relevant
CATEGORY_SCRAPERS = {
    "anime":    [HDHub4uScraper],
    "movie":    [HDHub4uScraper],
    "tv":       [HDHub4uScraper],
    "game":     [HDHub4uScraper],
    "software": [HDHub4uScraper],
    "ebook":    [HDHub4uScraper],
    "music":    [HDHub4uScraper],
}


class ScraperManager:
    """
    Runs scrapers concurrently using asyncio.gather, aggregates results,
    deduplicates, filters, scores, and caps at MAX_RESULTS.

    Usage:
        manager = ScraperManager()
        results = await manager.search(query, progress_callback=cb)
    """

    def __init__(self, max_results: int = 50):
        self.max_results = max_results

    def _select_scrapers(self, query: SearchQuery) -> List[BaseScraper]:
        """Choose scrapers based on category filter (or use all)."""
        if query.category and query.category in CATEGORY_SCRAPERS:
            types = CATEGORY_SCRAPERS[query.category]
            selected = [s for s in ALL_SCRAPERS if type(s) in types]
            return selected or ALL_SCRAPERS
        return ALL_SCRAPERS

    async def search(
        self,
        query: SearchQuery,
        progress_callback: Optional[Callable[[int, int, int, str], None]] = None,
    ) -> List[TorrentResult]:
        """
        Search all relevant scrapers concurrently.

        Args:
            query: Parsed SearchQuery object.
            progress_callback: Called with (done, total, found_so_far, scraper_name).

        Returns:
            Deduplicated, scored, filtered list of TorrentResult.
        """
        scrapers = self._select_scrapers(query)
        total = len(scrapers)
        done = 0
        aggregated: List[TorrentResult] = []
        lock = asyncio.Lock()

        connector = aiohttp.TCPConnector(
            limit=settings.SCRAPER_CONCURRENCY,
            ssl=False,
            enable_cleanup_closed=True,
        )

        async with aiohttp.ClientSession(connector=connector) as session:

            async def run_scraper(scraper: BaseScraper) -> None:
                nonlocal done
                try:
                    log.info(f"[Manager] Running {scraper.name}")
                    results = await scraper.search(query, session)
                    async with lock:
                        aggregated.extend(results)
                        done += 1
                        if progress_callback:
                            progress_callback(done, total, len(aggregated), scraper.name)
                except Exception as e:
                    log.error(f"[Manager] {scraper.name} failed: {e}")
                    async with lock:
                        done += 1
                        if progress_callback:
                            progress_callback(done, total, len(aggregated), f"{scraper.name} (failed)")

            await asyncio.gather(*[run_scraper(s) for s in scrapers])

        # Post-processing pipeline
        results = deduplicate(aggregated)
        results = self._apply_filters(results, query)
        results = score_results(results)
        results = results[: self.max_results]

        log.info(f"[Manager] Final: {len(results)} results for '{query.display_query}'")
        return results

    async def fetch_latest(self) -> List[TorrentResult]:
        """Aggregate latest uploads from all scrapers."""
        aggregated: List[TorrentResult] = []
        connector = aiohttp.TCPConnector(limit=settings.SCRAPER_CONCURRENCY, ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [s.fetch_latest(session) for s in ALL_SCRAPERS]
            results_list = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results_list:
                if isinstance(r, list):
                    aggregated.extend(r)

        return score_results(deduplicate(aggregated))[: self.max_results]

    async def fetch_trending(self) -> List[TorrentResult]:
        """Aggregate trending torrents from all scrapers."""
        aggregated: List[TorrentResult] = []
        connector = aiohttp.TCPConnector(limit=settings.SCRAPER_CONCURRENCY, ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [s.fetch_trending(session) for s in ALL_SCRAPERS]
            results_list = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results_list:
                if isinstance(r, list):
                    aggregated.extend(r)

        return score_results(deduplicate(aggregated))[: self.max_results]

    @staticmethod
    def _apply_filters(results: List[TorrentResult], query: SearchQuery) -> List[TorrentResult]:
        """Filter results by resolution, codec, and size constraints."""
        filtered = []
        for r in results:
            title_lower = r.title.lower()

            # Resolution filter
            if query.resolution:
                res = query.resolution.lower().replace("4k", "2160p")
                if res not in title_lower and query.resolution not in title_lower:
                    continue

            # Codec filter
            if query.codec and query.codec.lower() not in title_lower:
                continue

            # Size filters (approximate GB parsing)
            if query.min_size_gb or query.max_size_gb:
                size_gb = _parse_size_gb(r.size)
                if size_gb is not None:
                    if query.min_size_gb and size_gb < query.min_size_gb:
                        continue
                    if query.max_size_gb and size_gb > query.max_size_gb:
                        continue

            filtered.append(r)

        return filtered


def _parse_size_gb(size_str: str | None) -> float | None:
    """Convert a human-readable size string to GB float."""
    if not size_str:
        return None
    import re
    m = re.search(r"([\d.]+)\s*(gb|mb|kb|tb)", size_str.lower())
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2)
    return {"tb": val * 1024, "gb": val, "mb": val / 1024, "kb": val / (1024 * 1024)}.get(unit)


# Singleton
scraper_manager = ScraperManager(max_results=settings.MAX_RESULTS)

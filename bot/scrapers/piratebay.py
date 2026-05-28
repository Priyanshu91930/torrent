"""
piratebay.py — Scraper for The Pirate Bay via its JSON API.
TPB API: https://apibay.org/q.php?q=<query>&cat=0
"""

import re
from typing import List

import aiohttp

from bot.models import TorrentResult, SearchQuery
from bot.scrapers.base import BaseScraper
from bot.utils.logger import log

TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.openbittorrent.com:80",
    "udp://tracker.coppersurfer.tk:6969",
    "udp://exodus.desync.com:6969",
]
TR_STR = "&".join(f"tr={t}" for t in TRACKERS)

CAT_MAP = {
    "100": "audio", "101": "music", "102": "audio",
    "200": "video", "201": "movie", "202": "movie", "203": "movie",
    "205": "tv", "207": "tv",
    "400": "game", "401": "game", "402": "game", "403": "game",
    "600": "software", "601": "software",
    "500": "ebook",
}


class PirateBayScraper(BaseScraper):
    """
    Uses apibay.org JSON API for Pirate Bay searches.
    Free, no scraping required.
    """

    name = "PirateBay"
    base_url = "https://apibay.org"

    async def search(
        self, query: SearchQuery, session: aiohttp.ClientSession
    ) -> List[TorrentResult]:
        url = f"{self.base_url}/q.php"
        params = {"q": query.query, "cat": "0"}
        log.info(f"[{self.name}] Searching: {query.query}")
        text = await self._get(session, url, params=params)
        if not text:
            return []
        return self._parse(text)

    def _parse(self, text: str) -> List[TorrentResult]:
        try:
            import orjson
            items = orjson.loads(text)
        except Exception:
            import json
            try:
                items = json.loads(text)
            except Exception as e:
                log.error(f"[{self.name}] JSON parse error: {e}")
                return []

        if not isinstance(items, list):
            return []

        results = []
        for item in items:
            try:
                name = item.get("name", "")
                if name in ("No results returned", ""):
                    continue

                info_hash = item.get("info_hash", "")
                if not info_hash:
                    continue

                magnet = (
                    f"magnet:?xt=urn:btih:{info_hash}"
                    f"&dn={name.replace(' ', '+')}&{TR_STR}"
                )

                size_bytes = int(item.get("size", 0))
                size = self._format_size(size_bytes)
                seeders = int(item.get("seeders", 0))
                leechers = int(item.get("leechers", 0))
                cat_id = str(item.get("category", ""))
                category = CAT_MAP.get(cat_id, None)
                date = item.get("added", "")

                results.append(
                    TorrentResult(
                        title=name,
                        magnet=magnet,
                        size=size,
                        seeders=seeders,
                        leechers=leechers,
                        upload_date=date,
                        category=category,
                        source=self.name,
                    )
                )
            except Exception as e:
                log.debug(f"[{self.name}] Item parse error: {e}")

        log.info(f"[{self.name}] Found {len(results)} results")
        return results

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes >= 1024 ** 3:
            return f"{size_bytes / 1024 ** 3:.2f} GB"
        elif size_bytes >= 1024 ** 2:
            return f"{size_bytes / 1024 ** 2:.1f} MB"
        else:
            return f"{size_bytes / 1024:.0f} KB"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> List[TorrentResult]:
        url = f"{self.base_url}/precompiled/data_top100_recent.json"
        text = await self._get(session, url)
        if not text:
            return []
        return self._parse(text)

    async def fetch_trending(self, session: aiohttp.ClientSession) -> List[TorrentResult]:
        url = f"{self.base_url}/precompiled/data_top100_48h.json"
        text = await self._get(session, url)
        if not text:
            return []
        return self._parse(text)

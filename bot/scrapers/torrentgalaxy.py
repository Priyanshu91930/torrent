"""
torrentgalaxy.py — HTML scraper for TorrentGalaxy.to
Supports multi-page fetching via the `page` parameter.
"""

import asyncio
import re
from typing import List

import aiohttp
from bs4 import BeautifulSoup

from bot.models import TorrentResult, SearchQuery
from bot.scrapers.base import BaseScraper
from bot.utils.logger import log


class TorrentGalaxyScraper(BaseScraper):
    """
    Scrapes TorrentGalaxy search results.
    URL: https://torrentgalaxy.to/torrents.php?search=<query>
    """

    name = "TorrentGalaxy"
    base_url = "https://torrentgalaxy.to"

    CATEGORY_MAP = {
        "movies": "movie",
        "tv": "tv",
        "anime": "anime",
        "games": "game",
        "apps": "software",
        "books": "ebook",
        "music": "music",
        "xxx": "xxx",
    }

    # Max pages to fetch concurrently
    MAX_PAGES = 10

    async def search(
        self, query: SearchQuery, session: aiohttp.ClientSession
    ) -> List[TorrentResult]:
        log.info(f"[{self.name}] Searching: {query.query}")

        async def fetch_page(page: int) -> List[TorrentResult]:
            url = f"{self.base_url}/torrents.php"
            params = {
                "search": query.query,
                "lang": "0",
                "nox": "2",
                "sort": "seeders",
                "order": "desc",
                "page": page,
            }
            html = await self._get(session, url, params=params)
            if not html:
                return []
            return self._parse(html)

        # Fetch all pages concurrently
        tasks = [fetch_page(p) for p in range(self.MAX_PAGES)]
        pages = await asyncio.gather(*tasks, return_exceptions=True)

        all_results: List[TorrentResult] = []
        for page_results in pages:
            if isinstance(page_results, list):
                if not page_results:
                    break
                all_results.extend(page_results)

        log.info(f"[{self.name}] Found {len(all_results)} results across {self.MAX_PAGES} pages")
        return all_results

    def _parse(self, html: str) -> List[TorrentResult]:
        soup = BeautifulSoup(html, "lxml")
        results: List[TorrentResult] = []

        # TorrentGalaxy renders results in div.tgxtablerow
        rows = soup.select("div.tgxtablerow") or soup.select("tr.tgxtablecell")

        for row in rows:
            try:
                r = self._parse_row(row)
                if r:
                    results.append(r)
            except Exception as e:
                log.debug(f"[{self.name}] Row parse error: {e}")

        if not results:
            results = self._fallback_magnets(soup)

        log.info(f"[{self.name}] Found {len(results)} results")
        return results

    def _parse_row(self, row) -> TorrentResult | None:
        # Title
        title_el = row.select_one("a.txlight") or row.select_one(".tgxtablecell a")
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        if not title:
            return None

        # Magnet
        magnet_el = row.find("a", href=re.compile(r"^magnet:", re.I))
        magnet = magnet_el["href"] if magnet_el else None

        # Torrent download link
        torrent_el = row.find("a", href=re.compile(r"\.torrent", re.I))
        torrent_url = torrent_el["href"] if torrent_el else None
        if torrent_url and not torrent_url.startswith("http"):
            torrent_url = self.base_url + torrent_url

        # Size
        size_el = row.select_one("span.badge-secondary") or row.select_one(".tgxtablecell:nth-child(7)")
        size = size_el.get_text(strip=True) if size_el else None

        # Seeders / Leechers — usually green/red colored spans
        green = row.select("span.badge-success, font[color='green']")
        red = row.select("span.badge-danger, font[color='red']")
        seeders = int(green[0].get_text(strip=True)) if green else None
        leechers = int(red[0].get_text(strip=True)) if red else None

        # Date
        date_el = row.select_one("small") or row.select_one(".tgxtablecell:nth-child(10)")
        date = date_el.get_text(strip=True) if date_el else None

        # Category
        cat_el = row.select_one("span.label")
        category = None
        if cat_el:
            cat_text = cat_el.get_text(strip=True).lower()
            for k, v in self.CATEGORY_MAP.items():
                if k in cat_text:
                    category = v
                    break

        return TorrentResult(
            title=title,
            magnet=magnet,
            torrent_url=torrent_url,
            size=size,
            seeders=seeders,
            leechers=leechers,
            upload_date=date,
            category=category,
            source=self.name,
        )

    def _fallback_magnets(self, soup: BeautifulSoup) -> List[TorrentResult]:
        results = []
        for a in soup.find_all("a", href=re.compile(r"^magnet:")):
            title = a.get_text(strip=True) or "Unknown"
            results.append(TorrentResult(title=title, magnet=a["href"], source=self.name))
        return results

    async def fetch_latest(self, session: aiohttp.ClientSession) -> List[TorrentResult]:
        url = f"{self.base_url}/torrents.php"
        params = {"sort": "id", "order": "desc", "nox": "2"}
        html = await self._get(session, url, params=params)
        if not html:
            return []
        return self._parse(html)

    async def fetch_trending(self, session: aiohttp.ClientSession) -> List[TorrentResult]:
        url = f"{self.base_url}/torrents.php"
        params = {"sort": "views", "order": "desc", "nox": "2"}
        html = await self._get(session, url, params=params)
        if not html:
            return []
        return self._parse(html)

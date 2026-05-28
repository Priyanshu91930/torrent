"""
nyaa.py — Scraper for Nyaa.si (anime torrents) via RSS feed.
Supports multi-page fetching via the `p` parameter.
"""

import asyncio
import xml.etree.ElementTree as ET
from typing import List

import aiohttp

from bot.models import TorrentResult, SearchQuery
from bot.scrapers.base import BaseScraper
from bot.utils.logger import log


class NyaaScraper(BaseScraper):
    """
    Parses Nyaa.si RSS feed for anime/manga torrent results.
    RSS URL: https://nyaa.si/?page=rss&q=<query>&c=0_0&f=0
    """

    name = "Nyaa"
    base_url = "https://nyaa.si"

    # Nyaa RSS namespaces
    NS = {"nyaa": "https://nyaa.si/xmlns/nyaa"}

    # Max pages to fetch (75 results per page on Nyaa RSS)
    MAX_PAGES = 10

    async def search(
        self, query: SearchQuery, session: aiohttp.ClientSession
    ) -> List[TorrentResult]:
        log.info(f"[{self.name}] Searching: {query.query}")

        async def fetch_page(page: int) -> List[TorrentResult]:
            params = {
                "page": "rss",
                "q": query.query,
                "c": "0_0",
                "f": "0",
                "p": page,
            }
            xml_text = await self._get(session, self.base_url + "/", params=params)
            if not xml_text:
                return []
            return self._parse_rss(xml_text)

        # Fetch all pages concurrently
        tasks = [fetch_page(p) for p in range(1, self.MAX_PAGES + 1)]
        pages = await asyncio.gather(*tasks, return_exceptions=True)

        all_results: List[TorrentResult] = []
        for page_results in pages:
            if isinstance(page_results, list):
                if not page_results:
                    break
                all_results.extend(page_results)

        log.info(f"[{self.name}] Found {len(all_results)} results across {self.MAX_PAGES} pages")
        return all_results

    def _parse_rss(self, xml_text: str) -> List[TorrentResult]:
        results: List[TorrentResult] = []
        try:
            root = ET.fromstring(xml_text)
            channel = root.find("channel")
            if channel is None:
                return []

            for item in channel.findall("item"):
                try:
                    title = item.findtext("title", "").strip()
                    magnet = None
                    torrent_url = None

                    link = item.findtext("link", "").strip()
                    if link.endswith(".torrent"):
                        torrent_url = link
                    elif link.startswith("magnet:"):
                        magnet = link

                    # Nyaa provides magnet in <nyaa:magnetUri>
                    magnet_el = item.find("nyaa:magnetUri", self.NS)
                    if magnet_el is not None and magnet_el.text:
                        magnet = magnet_el.text.strip()

                    size_el = item.find("nyaa:size", self.NS)
                    size = size_el.text.strip() if size_el is not None else None

                    seeders_el = item.find("nyaa:seeders", self.NS)
                    seeders = int(seeders_el.text) if seeders_el is not None else None

                    leechers_el = item.find("nyaa:leechers", self.NS)
                    leechers = int(leechers_el.text) if leechers_el is not None else None

                    date = item.findtext("pubDate", "").strip()

                    results.append(
                        TorrentResult(
                            title=title,
                            magnet=magnet,
                            torrent_url=torrent_url,
                            size=size,
                            seeders=seeders,
                            leechers=leechers,
                            upload_date=date,
                            category="anime",
                            source=self.name,
                        )
                    )
                except Exception as e:
                    log.debug(f"[{self.name}] Item parse error: {e}")

        except ET.ParseError as e:
            log.error(f"[{self.name}] XML parse error: {e}")

        log.info(f"[{self.name}] Found {len(results)} results")
        return results

    async def fetch_latest(self, session: aiohttp.ClientSession) -> List[TorrentResult]:
        params = {"page": "rss", "c": "0_0", "f": "0"}
        xml_text = await self._get(session, self.base_url + "/", params=params)
        if not xml_text:
            return []
        return self._parse_rss(xml_text)

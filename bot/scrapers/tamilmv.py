"""
tamilmv.py — Scraper for 1TamilMV (https://www.1tamilmv.futbol)
Extracts torrent results from search pages using BeautifulSoup.
"""

import re
from typing import List

import aiohttp
from bs4 import BeautifulSoup

from bot.models import TorrentResult, SearchQuery
from bot.scrapers.base import BaseScraper
from bot.utils.logger import log


class TamilMVScraper(BaseScraper):
    """
    Scrapes 1TamilMV search results.
    URL pattern: https://www.1tamilmv.futbol/search/?q=<query>
    """

    name = "TamilMV"
    base_url = "https://www.1tamilmv.futbol"

    async def search(
        self, query: SearchQuery, session: aiohttp.ClientSession
    ) -> List[TorrentResult]:
        search_url = f"{self.base_url}/search/"
        params = {"q": query.query}
        log.info(f"[{self.name}] Searching: {query.query}")

        html = await self._get(session, search_url, params=params)
        if not html:
            return []

        return self._parse(html)

    def _parse(self, html: str) -> List[TorrentResult]:
        """Parse the search results page HTML."""
        soup = BeautifulSoup(html, "lxml")
        results: List[TorrentResult] = []

        # TamilMV shows posts in .entry-content or article tags
        posts = soup.select("article") or soup.select(".post") or soup.select(".entry")

        if not posts:
            # Fallback: find all magnet links anywhere on page
            return self._fallback_parse(soup)

        for post in posts:
            try:
                result = self._parse_post(post)
                if result:
                    results.append(result)
            except Exception as e:
                log.debug(f"[{self.name}] Parse error for post: {e}")

        log.info(f"[{self.name}] Found {len(results)} results")
        return results

    def _parse_post(self, post) -> TorrentResult | None:
        """Extract one TorrentResult from an article/post element."""
        # Title
        title_el = (
            post.select_one("h1 a")
            or post.select_one("h2 a")
            or post.select_one(".entry-title a")
            or post.select_one("h3 a")
        )
        if not title_el:
            return None
        title = title_el.get_text(strip=True)

        # Post detail URL — we may need to follow it for the magnet
        post_url = title_el.get("href", "")

        # Magnet link — may be directly in search listing
        magnet_tag = post.find("a", href=re.compile(r"^magnet:", re.I))
        magnet = magnet_tag["href"] if magnet_tag else None

        # Thumbnail
        img = post.select_one("img")
        thumbnail = img["src"] if img and img.get("src") else None

        # Size, seeders, leechers often appear in table or span elements
        size = self._extract_text(post, ["size", "file size"])
        seeders = self._extract_int(post, ["seeders", "seeds"])
        leechers = self._extract_int(post, ["leechers", "peers"])
        date = self._extract_text(post, ["date", "time", "posted"])

        return TorrentResult(
            title=title,
            magnet=magnet,
            torrent_url=post_url if post_url.endswith(".torrent") else None,
            size=size,
            seeders=seeders,
            leechers=leechers,
            upload_date=date,
            category=self._guess_category(title),
            source=self.name,
            thumbnail=thumbnail,
        )

    def _fallback_parse(self, soup: BeautifulSoup) -> List[TorrentResult]:
        """
        Fallback: collect every magnet link found anywhere on the page
        with the nearest heading as the title.
        """
        results: List[TorrentResult] = []
        seen = set()

        for a in soup.find_all("a", href=re.compile(r"^magnet:", re.I)):
            magnet = a["href"]
            if magnet in seen:
                continue
            seen.add(magnet)

            # Walk up to find the nearest heading
            parent = a
            title = ""
            for _ in range(6):
                heading = parent.find_previous(["h1", "h2", "h3", "h4"])
                if heading:
                    title = heading.get_text(strip=True)
                    break
                parent = parent.parent
                if parent is None:
                    break

            if not title:
                title = a.get_text(strip=True) or "Unknown Title"

            results.append(
                TorrentResult(
                    title=title,
                    magnet=magnet,
                    source=self.name,
                    category=self._guess_category(title),
                )
            )

        log.info(f"[{self.name}] Fallback found {len(results)} magnets")
        return results

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(tag, keywords: list) -> str | None:
        """Search for a text node near a keyword label."""
        text = tag.get_text(" ", strip=True).lower()
        for kw in keywords:
            idx = text.find(kw)
            if idx != -1:
                snippet = text[idx + len(kw):idx + len(kw) + 20].strip(": \n")
                return snippet or None
        return None

    @staticmethod
    def _extract_int(tag, keywords: list) -> int | None:
        text = tag.get_text(" ", strip=True).lower()
        for kw in keywords:
            idx = text.find(kw)
            if idx != -1:
                snippet = text[idx + len(kw):idx + len(kw) + 10]
                nums = re.findall(r"\d+", snippet)
                if nums:
                    return int(nums[0])
        return None

    @staticmethod
    def _guess_category(title: str) -> str:
        t = title.lower()
        if any(k in t for k in ["movie", "film", "720p", "1080p", "4k", "2160p", "bluray", "bdrip"]):
            return "movie"
        if any(k in t for k in ["s0", "s1", "s2", "s3", "episode", "ep", "web series"]):
            return "tv"
        if any(k in t for k in ["anime", "dubbed", "subbed"]):
            return "anime"
        if any(k in t for k in ["game", "gta", "ps4", "ps5", "xbox"]):
            return "game"
        if any(k in t for k in ["software", "crack", "keygen", "iso", "portable"]):
            return "software"
        return "movie"  # TamilMV is predominantly movies

    async def fetch_latest(self, session: aiohttp.ClientSession) -> List[TorrentResult]:
        html = await self._get(session, self.base_url)
        if not html:
            return []
        return self._parse(html)

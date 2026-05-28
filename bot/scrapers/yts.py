"""
yts.py — Scraper for YTS.mx movie API (JSON, no scraping needed).
API docs: https://yts.mx/api
"""

from typing import List

import aiohttp

from bot.models import TorrentResult, SearchQuery
from bot.scrapers.base import BaseScraper
from bot.utils.logger import log


class YTSScraper(BaseScraper):
    """
    Uses the official YTS JSON API to fetch movie torrent information.
    Only supports movie category; skipped for other categories.
    """

    name = "YTS"
    base_url = "https://yts.mx/api/v2"

    async def search(
        self, query: SearchQuery, session: aiohttp.ClientSession
    ) -> List[TorrentResult]:
        # YTS is movies-only; skip for non-movie queries
        if query.category and query.category not in ("movie", None):
            return []

        url = f"{self.base_url}/list_movies.json"
        params = {
            "query_term": query.query,
            "limit": 20,
            "sort_by": "seeds",
            "order_by": "desc",
        }
        if query.resolution == "4k":
            params["quality"] = "2160p"
        elif query.resolution == "1080p":
            params["quality"] = "1080p"

        log.info(f"[{self.name}] Searching: {query.query}")
        text = await self._get(session, url, params=params)
        if not text:
            return []

        try:
            import orjson
            data = orjson.loads(text)
        except Exception:
            import json
            try:
                data = json.loads(text)
            except Exception as e:
                log.error(f"[{self.name}] JSON parse error: {e}")
                return []

        return self._parse_json(data)

    def _parse_json(self, data: dict) -> List[TorrentResult]:
        results: List[TorrentResult] = []

        movies = (
            data.get("data", {}).get("movies") or []
        )

        for movie in movies:
            title = movie.get("title_long", movie.get("title", "Unknown"))
            thumbnail = movie.get("medium_cover_image", "")
            year = movie.get("year", "")
            imdb = movie.get("imdb_code", "")

            for torrent in movie.get("torrents", []):
                quality = torrent.get("quality", "")
                codec = torrent.get("video_codec", "")
                size = torrent.get("size", "")
                seeders = torrent.get("seeds", 0)
                leechers = torrent.get("peers", 0)
                magnet = self._build_magnet(torrent, title)
                torrent_url = torrent.get("url", "")
                date_uploaded = torrent.get("date_uploaded", "")

                full_title = f"{title} [{quality}] [{codec}]"
                results.append(
                    TorrentResult(
                        title=full_title,
                        magnet=magnet,
                        torrent_url=torrent_url,
                        size=size,
                        seeders=seeders,
                        leechers=leechers,
                        upload_date=date_uploaded,
                        category="movie",
                        source=self.name,
                        thumbnail=thumbnail,
                        imdb_id=imdb,
                    )
                )

        log.info(f"[{self.name}] Found {len(results)} results")
        return results

    @staticmethod
    def _build_magnet(torrent: dict, title: str) -> str | None:
        """Build magnet URI from hash if available."""
        hash_ = torrent.get("hash")
        if not hash_:
            return None
        trackers = [
            "udp://open.demonii.com:1337/announce",
            "udp://tracker.openbittorrent.com:80",
            "udp://tracker.coppersurfer.tk:6969",
            "udp://glotorrents.pw:6969/announce",
            "udp://tracker.opentrackr.org:1337/announce",
            "udp://torrent.gresille.org:80/announce",
            "udp://p4p.arenabg.com:1337",
            "udp://tracker.leechers-paradise.org:6969",
        ]
        tr = "&".join(f"tr={t}" for t in trackers)
        safe_title = title.replace(" ", "+")
        return f"magnet:?xt=urn:btih:{hash_}&dn={safe_title}&{tr}"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> List[TorrentResult]:
        url = f"{self.base_url}/list_movies.json"
        params = {"limit": 20, "sort_by": "date_added", "order_by": "desc"}
        text = await self._get(session, url, params=params)
        if not text:
            return []
        try:
            import orjson
            return self._parse_json(orjson.loads(text))
        except Exception:
            return []

    async def fetch_trending(self, session: aiohttp.ClientSession) -> List[TorrentResult]:
        url = f"{self.base_url}/list_movies.json"
        params = {"limit": 20, "sort_by": "download_count", "order_by": "desc"}
        text = await self._get(session, url, params=params)
        if not text:
            return []
        try:
            import orjson
            return self._parse_json(orjson.loads(text))
        except Exception:
            return []

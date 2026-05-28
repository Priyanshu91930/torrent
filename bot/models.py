"""
models.py — Shared data models used across the entire bot.
Using Python dataclasses for zero-dependency simplicity.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TorrentResult:
    """Represents a single torrent search result."""

    title: str
    magnet: Optional[str] = None
    torrent_url: Optional[str] = None
    size: Optional[str] = None
    seeders: Optional[int] = None
    leechers: Optional[int] = None
    upload_date: Optional[str] = None
    category: Optional[str] = None
    source: Optional[str] = None          # Which scraper found this
    health: Optional[str] = None          # Computed by health.py
    thumbnail: Optional[str] = None       # URL to poster/thumbnail
    imdb_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "magnet": self.magnet,
            "torrent_url": self.torrent_url,
            "size": self.size,
            "seeders": self.seeders,
            "leechers": self.leechers,
            "upload_date": self.upload_date,
            "category": self.category,
            "source": self.source,
            "health": self.health,
            "thumbnail": self.thumbnail,
            "imdb_id": self.imdb_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TorrentResult":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class SearchQuery:
    """Parsed search query with optional filters."""

    raw: str
    query: str = ""
    category: Optional[str] = None        # movie, anime, game, software, tv
    resolution: Optional[str] = None      # 4k, 1080p, 720p
    codec: Optional[str] = None           # x265, x264, h264
    min_size_gb: Optional[float] = None
    max_size_gb: Optional[float] = None
    user_id: int = 0

    CATEGORY_KEYWORDS = {
        "movie": "movie",
        "film": "movie",
        "tv": "tv",
        "show": "tv",
        "series": "tv",
        "anime": "anime",
        "game": "game",
        "software": "software",
        "app": "software",
        "iso": "software",
        "ebook": "ebook",
        "book": "ebook",
        "music": "music",
        "audio": "music",
        "xxx": "xxx",
    }

    RESOLUTION_KEYWORDS = {"4k", "2160p", "1080p", "720p", "480p", "hd"}
    CODEC_KEYWORDS = {"x265", "x264", "h265", "h264", "hevc", "avc", "av1"}

    def __post_init__(self):
        self._parse()

    def _parse(self):
        tokens = self.raw.lower().split()
        remaining = []

        for token in tokens:
            if token in self.CATEGORY_KEYWORDS:
                self.category = self.CATEGORY_KEYWORDS[token]
            elif token in self.RESOLUTION_KEYWORDS:
                self.resolution = token
            elif token in self.CODEC_KEYWORDS:
                self.codec = token
            elif token.startswith("min:"):
                try:
                    self.min_size_gb = float(token[4:])
                except ValueError:
                    pass
            elif token.startswith("max:"):
                try:
                    self.max_size_gb = float(token[4:])
                except ValueError:
                    pass
            else:
                remaining.append(token)

        self.query = " ".join(remaining).strip() or self.raw.strip()

    @property
    def display_query(self) -> str:
        parts = [self.query]
        if self.category:
            parts.append(f"[{self.category}]")
        if self.resolution:
            parts.append(self.resolution.upper())
        if self.codec:
            parts.append(self.codec.upper())
        return " ".join(parts)

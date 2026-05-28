"""
dedup.py — Magnet link deduplication by info-hash extraction.
"""

import re
from typing import List
from bot.models import TorrentResult


def _extract_infohash(magnet: str) -> str:
    """Pull the 40-char hex (or 32-char base32) info-hash from a magnet URI."""
    match = re.search(r"urn:btih:([a-fA-F0-9]{40}|[A-Z2-7]{32})", magnet)
    return match.group(1).upper() if match else magnet


def deduplicate(results: List[TorrentResult]) -> List[TorrentResult]:
    """
    Remove duplicate torrent results based on magnet info-hash.
    Keeps the first occurrence (highest seeder count if pre-sorted).
    """
    seen: set = set()
    unique: List[TorrentResult] = []

    for r in results:
        key = _extract_infohash(r.magnet) if r.magnet else r.title.lower()
        if key not in seen:
            seen.add(key)
            unique.append(r)

    return unique

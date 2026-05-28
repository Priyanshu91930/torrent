"""
health.py — Torrent health scoring based on seeder/leecher ratio.
"""

from bot.models import TorrentResult


def compute_health(result: TorrentResult) -> str:
    """
    Return a health label and emoji based on seeder count and ratio.

    Ratings:
        ⭐⭐⭐ Excellent  — 100+ seeders
        ⭐⭐  Good       — 20–99 seeders
        ⭐   Fair       — 5–19 seeders
        💀   Dead       — 0–4 seeders
    """
    s = result.seeders or 0

    if s >= 100:
        result.health = "⭐⭐⭐ Excellent"
    elif s >= 20:
        result.health = "⭐⭐ Good"
    elif s >= 5:
        result.health = "⭐ Fair"
    else:
        result.health = "💀 Dead"

    return result.health


def score_results(results: list) -> list:
    """Apply health scoring to all results and sort by seeder count desc."""
    for r in results:
        compute_health(r)
    return sorted(results, key=lambda r: r.seeders or 0, reverse=True)

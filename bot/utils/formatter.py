"""
formatter.py — Telegram HTML message formatter for torrent results.
"""

import html
from typing import List

from bot.models import TorrentResult, SearchQuery


# ── Category icons ────────────────────────────────────────────────────────────
CATEGORY_ICONS = {
    "movie": "🎬",
    "tv": "📺",
    "anime": "🎌",
    "game": "🎮",
    "software": "💿",
    "ebook": "📚",
    "music": "🎵",
    "xxx": "🔞",
    None: "📦",
}


def category_icon(category: str | None) -> str:
    return CATEGORY_ICONS.get(category, "📦")


def _safe(text: str | None, fallback: str = "N/A") -> str:
    if not text:
        return fallback
    return html.escape(str(text))


# ── Single result card ────────────────────────────────────────────────────────

def format_result(result: TorrentResult, index: int, total: int) -> str:
    """
    Build a rich Telegram HTML message for one torrent result.

    Example output:
        🎬 <b>Movie Title 2026</b>
        📦 Size: 2.4 GB  |  Source: TamilMV
        🌱 Seeders: 542   📥 Leechers: 32
        ⭐ Health: Excellent
        📅 2026-05-01
        ━━━━━━━━━━━━━━━━━
        🔗 Result 3 of 15
    """
    icon = category_icon(result.category)
    title = _safe(result.title)
    size = _safe(result.size)
    date = _safe(result.upload_date)
    source = _safe(result.source)

    if source.lower() == "hdhub4u":
        lines = [
            f"{icon} <b>{title}</b>",
            f"",
            f"📦 <b>Size:</b> {size}  |  🌐 <b>Source:</b> {source}",
            f"📅 <b>Date:</b> {date}",
            f"",
            f"━━━━━━━━━━━━━━━━━━",
            f"🔗 <i>Result {index} of {total}</i>",
        ]
    else:
        seeders = result.seeders if result.seeders is not None else "?"
        leechers = result.leechers if result.leechers is not None else "?"
        health = _safe(result.health, "Unknown")
        lines = [
            f"{icon} <b>{title}</b>",
            f"",
            f"📦 <b>Size:</b> {size}  |  🌐 <b>Source:</b> {source}",
            f"🌱 <b>Seeders:</b> {seeders}   📥 <b>Leechers:</b> {leechers}",
            f"⭐ <b>Health:</b> {health}",
            f"📅 <b>Date:</b> {date}",
            f"",
            f"━━━━━━━━━━━━━━━━━━",
            f"🔗 <i>Result {index} of {total}</i>",
        ]
    return "\n".join(lines)


def format_magnet_block(magnet: str) -> str:
    """Format the magnet link or direct link for display."""
    if not magnet:
        return "❌ <i>No link available</i>"
    if magnet.startswith("http"):
        return f"🔗 <b>Download Link:</b> <code>{html.escape(magnet)}</code>"
    short = magnet[:60] + "…" if len(magnet) > 60 else magnet
    return f"🧲 <code>{html.escape(short)}</code>"


# ── Search summary ────────────────────────────────────────────────────────────

def format_search_summary(query: SearchQuery, count: int, cached: bool = False) -> str:
    cache_tag = " ⚡ <i>(cached)</i>" if cached else ""
    return (
        f"🔍 <b>Search:</b> <code>{_safe(query.display_query)}</code>{cache_tag}\n"
        f"📊 Found <b>{count}</b> result(s)"
    )


# ── Progress bar ──────────────────────────────────────────────────────────────

def format_progress(current: int, total: int, found: int, stage: str = "") -> str:
    """
    Render a dynamic progress bar.

    Example:
        🔍 Searching torrents...
        ██████░░░░ 60%
        Found: 120 magnet links
    """
    pct = int((current / max(total, 1)) * 100)
    filled = int(pct / 10)
    bar = "█" * filled + "░" * (10 - filled)
    stage_line = f"\n🌐 <i>{html.escape(stage)}</i>" if stage else ""
    return (
        f"🔍 <b>Searching torrents...</b>{stage_line}\n"
        f"<code>{bar}</code> {pct}%\n"
        f"✅ Found: <b>{found}</b> magnet link(s)"
    )


# ── Stats ─────────────────────────────────────────────────────────────────────

def format_stats(stats: dict) -> str:
    uptime = stats.get("uptime", "N/A")
    searches = stats.get("total_searches", 0)
    users = stats.get("total_users", 0)
    cache_size = stats.get("cache_size", 0)
    return (
        f"📊 <b>Bot Statistics</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⏱ <b>Uptime:</b> {uptime}\n"
        f"🔍 <b>Total Searches:</b> {searches}\n"
        f"👥 <b>Unique Users:</b> {users}\n"
        f"⚡ <b>Cache Size:</b> {cache_size} entries"
    )


def format_help() -> str:
    return (
        "🤖 <b>Torrent Search Bot — Commands</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔍 <b>/search</b> <code>&lt;query&gt; [filters]</code>\n"
        "   Search torrents. Filters:\n"
        "   <code>movie | tv | anime | game | software</code>\n"
        "   <code>4k | 1080p | 720p</code>\n"
        "   <code>x265 | x264</code>\n"
        "   <code>min:2 max:10</code> (size in GB)\n\n"
        "📈 <b>/top</b> — Trending torrents\n"
        "🆕 <b>/latest</b> — Latest uploads\n"
        "📊 <b>/stats</b> — Bot usage statistics\n"
        "⭐ <b>/save</b> <code>&lt;index&gt;</code> — Save a result\n"
        "📋 <b>/myfavs</b> — View saved torrents\n"
        "📤 <b>/export</b> — Export magnets as TXT\n"
        "❌ <b>/cancel</b> — Cancel ongoing search\n\n"
        "<i>Examples:</i>\n"
        "<code>/search 2026 movie 4k</code>\n"
        "<code>/search gta v game</code>\n"
        "<code>/search ubuntu iso software</code>"
    )

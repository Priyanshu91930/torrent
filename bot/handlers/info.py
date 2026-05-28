"""
info.py — Handlers for /help, /stats, /top, /latest commands.
"""

import time
from datetime import timedelta

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from bot.utils.formatter import format_help, format_stats, format_result
from bot.db.models import db
from bot.utils.cache import torrent_cache
from bot.scrapers.manager import scraper_manager
from bot.handlers.search import set_session, send_page
from bot.models import SearchQuery
from bot.utils.logger import log

# Bot start time for uptime calculation
_start_time = time.time()


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the help message."""
    await update.message.reply_text(format_help(), parse_mode=ParseMode.HTML)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show bot statistics."""
    uptime_secs = int(time.time() - _start_time)
    uptime_str = str(timedelta(seconds=uptime_secs))

    searches = await db.get_search_count()
    users = await db.get_user_count()

    stats = {
        "uptime": uptime_str,
        "total_searches": searches,
        "total_users": users,
        "cache_size": torrent_cache.size,
    }

    await update.message.reply_text(format_stats(stats), parse_mode=ParseMode.HTML)


async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show trending torrents."""
    user_id = update.effective_user.id
    msg = await update.message.reply_text(
        "📈 Fetching trending torrents…", parse_mode=ParseMode.HTML
    )

    try:
        results = await scraper_manager.fetch_trending()
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")
        return

    if not results:
        await msg.edit_text("😔 Could not fetch trending torrents right now.")
        return

    query = SearchQuery(raw="trending")
    set_session(user_id, results, query)
    await msg.edit_text(
        f"📈 <b>Trending Torrents</b> — {len(results)} found", parse_mode=ParseMode.HTML
    )
    await send_page(update.message, user_id)


async def latest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show latest uploaded torrents."""
    user_id = update.effective_user.id
    msg = await update.message.reply_text(
        "🆕 Fetching latest uploads…", parse_mode=ParseMode.HTML
    )

    try:
        results = await scraper_manager.fetch_latest()
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")
        return

    if not results:
        await msg.edit_text("😔 Could not fetch latest torrents right now.")
        return

    query = SearchQuery(raw="latest")
    set_session(user_id, results, query)
    await msg.edit_text(
        f"🆕 <b>Latest Uploads</b> — {len(results)} found", parse_mode=ParseMode.HTML
    )
    await send_page(update.message, user_id)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome new users."""
    user = update.effective_user
    await db.upsert_user(
        user.id,
        username=user.username or "",
        first_name=user.first_name or "",
    )

    await update.message.reply_text(
        f"👋 Welcome, <b>{user.first_name}</b>!\n\n"
        f"I can search <b>5 torrent sites</b> simultaneously and find magnet links for you.\n\n"
        f"Try: <code>/search Avengers 4k x265</code>\n\n"
        f"Type /help to see all commands.",
        parse_mode=ParseMode.HTML,
    )

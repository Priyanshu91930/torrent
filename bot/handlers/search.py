"""
search.py — Handler for the /search command.
Coordinates progress display, scraping, caching, and paginated result sending.
"""

import asyncio
from typing import Dict, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from bot.models import TorrentResult, SearchQuery
from bot.scrapers.manager import scraper_manager
from bot.utils.cache import torrent_cache
from bot.utils.formatter import (
    format_result, format_magnet_block, format_search_summary, format_progress
)
from bot.utils.progress import ProgressUpdater
from bot.utils.rate_limiter import rate_limiter
from bot.utils.logger import log
from bot.db.models import db
from bot.config import settings

# ── In-memory session store (user_id → search state) ─────────────────────────
# Stores results and current page index per user
_sessions: Dict[int, dict] = {}
_active_searches: Dict[int, bool] = {}   # Tracks cancellable searches


def get_session(user_id: int) -> dict | None:
    return _sessions.get(user_id)


def set_session(user_id: int, results: List[TorrentResult], query: SearchQuery) -> None:
    _sessions[user_id] = {
        "results": results,
        "page": 0,
        "query": query,
    }


# ── Keyboard builder ──────────────────────────────────────────────────────────

def build_keyboard(user_id: int) -> InlineKeyboardMarkup:
    session = _sessions.get(user_id, {})
    page = session.get("page", 0)
    results = session.get("results", [])
    total = len(results)
    per_page = settings.RESULTS_PER_PAGE

    current = results[page] if page < total else None
    has_prev = page > 0
    has_next = page < total - 1

    nav_row = []
    if has_prev:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"page:prev:{user_id}"))
    if total > 1:
        nav_row.append(InlineKeyboardButton(f"{page + 1}/{total}", callback_data="noop"))
    if has_next:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"page:next:{user_id}"))

    action_row = []
    if current and current.magnet:
        is_http = current.magnet.startswith("http")
        copy_label = "📋 Copy Link" if is_http else "📋 Copy Magnet"
        action_row.append(
            InlineKeyboardButton(copy_label, callback_data=f"copy:{user_id}")
        )
        if settings.STRING_SESSION and settings.LEECH_GROUP_ID:
            leech_label = "📥 Leech Link" if is_http else "📥 Send to Leech"
            action_row.append(
                InlineKeyboardButton(leech_label, callback_data=f"leech:{user_id}")
            )

    export_row = [
        InlineKeyboardButton("📤 Export All", callback_data=f"export:{user_id}"),
        InlineKeyboardButton("⭐ Save", callback_data=f"save:{user_id}"),
    ]
    if current and current.magnet:
        if settings.STRING_SESSION and settings.LEECH_GROUP_ID:
            export_row.insert(0, InlineKeyboardButton("📥 Leech All", callback_data=f"leechall:{user_id}"))

    rows = []
    if nav_row:
        rows.append(nav_row)
    if action_row:
        rows.append(action_row)
    rows.append(export_row)

    return InlineKeyboardMarkup(rows)


# ── Main search handler ───────────────────────────────────────────────────────

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /search <query> command."""
    user = update.effective_user
    user_id = user.id
    message = update.message

    # ── Parse input ──────────────────────────────────────────────────────────
    if not context.args:
        await message.reply_text(
            "❓ Usage: <code>/search &lt;query&gt; [movie|anime|4k|x265...]</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    raw = " ".join(context.args)
    query = SearchQuery(raw=raw, user_id=user_id)

    # ── Rate limiting ─────────────────────────────────────────────────────────
    if not await rate_limiter.check(user_id):
        wait = rate_limiter.time_until_reset(user_id)
        await message.reply_text(
            f"⏳ You're searching too fast! Please wait <b>{wait}s</b> before trying again.",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── Ban check ─────────────────────────────────────────────────────────────
    if await db.is_banned(user_id):
        await message.reply_text("🚫 You have been banned from using this bot.")
        return

    # ── Register user ─────────────────────────────────────────────────────────
    await db.upsert_user(user_id, user.username or "", user.first_name or "")

    # ── Check cache ───────────────────────────────────────────────────────────
    cached = await torrent_cache.get(query.raw)
    if cached:
        log.info(f"[Search] Cache HIT for '{query.raw}' (user {user_id})")
        results: List[TorrentResult] = cached
        set_session(user_id, results, query)

        summary = format_search_summary(query, len(results), cached=True)
        await message.reply_text(summary, parse_mode=ParseMode.HTML)

        if results:
            await send_page(message, user_id)
        return

    # ── Send initial progress message ─────────────────────────────────────────
    progress_msg = await message.reply_text(
        format_progress(0, len(scraper_manager._select_scrapers(query)), 0, "Initializing…"),
        parse_mode=ParseMode.HTML,
    )

    # ── Cancellation flag ─────────────────────────────────────────────────────
    _active_searches[user_id] = True
    updater = ProgressUpdater(progress_msg, update_interval=1.2)

    found_count = 0
    def progress_cb(done: int, total: int, found: int, stage: str) -> None:
        nonlocal found_count
        found_count = found
        updater.advance(0, found=found, stage=stage)
        # If user cancelled, we can't stop gather but we flag it
        if not _active_searches.get(user_id, True):
            raise asyncio.CancelledError("User cancelled")

    try:
        await updater.start(total=len(ALL_SCRAPERS_COUNT := scraper_manager._select_scrapers(query)))

        results = await scraper_manager.search(query, progress_callback=progress_cb)
    except asyncio.CancelledError:
        await updater.stop()
        await progress_msg.edit_text("❌ Search cancelled.")
        return
    except Exception as e:
        await updater.stop()
        log.error(f"[Search] Unexpected error: {e}")
        await progress_msg.edit_text(f"❌ Search failed: {e}")
        return
    finally:
        await updater.stop()
        _active_searches.pop(user_id, None)

    # ── Cache results ─────────────────────────────────────────────────────────
    if results:
        await torrent_cache.set(query.raw, results)

    # ── Log to DB ─────────────────────────────────────────────────────────────
    await db.log_search(user_id, query.raw, len(results))
    await db.increment_search_count(user_id)

    # ── Send summary ──────────────────────────────────────────────────────────
    summary = format_search_summary(query, len(results))
    await progress_msg.edit_text(summary, parse_mode=ParseMode.HTML)

    if not results:
        await message.reply_text("😔 No results found. Try a different query.")
        return

    # ── Store session & send first page ───────────────────────────────────────
    set_session(user_id, results, query)
    await send_page(message, user_id)


# ── Page sender ───────────────────────────────────────────────────────────────

async def send_page(message: Message, user_id: int) -> None:
    """Send the current result page to the user."""
    session = _sessions.get(user_id)
    if not session:
        return

    results = session["results"]
    page = session["page"]
    total = len(results)

    if page >= total:
        return

    result = results[page]
    text = format_result(result, page + 1, total)
    magnet_block = format_magnet_block(result.magnet)
    full_text = text + "\n\n" + magnet_block

    keyboard = build_keyboard(user_id)

    # Send thumbnail if available
    if result.thumbnail:
        try:
            await message.reply_photo(
                photo=result.thumbnail,
                caption=full_text[:1024],  # Telegram caption limit
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            return
        except Exception:
            pass  # Fall back to text if image fails

    await message.reply_text(
        full_text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


# ── Cancel handler ────────────────────────────────────────────────────────────

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id in _active_searches:
        _active_searches[user_id] = False
        await update.message.reply_text("🛑 Cancelling search...")
    else:
        await update.message.reply_text("ℹ️ No active search to cancel.")

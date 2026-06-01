"""
report.py — Handler for the /report command.
Runs a quality diagnostic report to check for posts missing 480p/720p/1080p qualities.
"""

import asyncio
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from bot.models import SearchQuery
from bot.scrapers.hdhub4u import HDHub4uScraper
from bot.utils.rate_limiter import rate_limiter
from bot.db.models import db
from bot.config import settings
from bot.utils.logger import log

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /report <query> command."""
    user = update.effective_user
    user_id = user.id
    message = update.message

    if not context.args:
        await message.reply_text(
            "❓ Usage: <code>/report &lt;query&gt;</code>\n"
            "Example: <code>/report series</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    raw = " ".join(context.args)
    query = SearchQuery(raw=raw, user_id=user_id)

    # Rate limiting
    if not await rate_limiter.check(user_id):
        wait = rate_limiter.time_until_reset(user_id)
        await message.reply_text(
            f"⏳ Please wait <b>{wait}s</b> before running another report.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Ban check
    if await db.is_banned(user_id):
        await message.reply_text("🚫 You have been banned from using this bot.")
        return

    # Send initial status
    status_msg = await message.reply_text(
        "📊 <b>Generating Quality Diagnostic Report...</b>\n"
        "Please wait, scanning the website pages to identify posts missing 480p/720p/1080p links.",
        parse_mode=ParseMode.HTML,
    )

    try:
        scraper = HDHub4uScraper(
            timeout=settings.REQUEST_TIMEOUT,
            max_retries=settings.MAX_RETRIES,
            proxy=settings.HTTP_PROXY,
        )
        # Fetching up to 2 pages (60 hits) of the newest posts to scan
        report_data = await scraper.run_quality_report(query, max_pages_to_check=2)
        
        total_website = report_data.get("total_website_items", 0)
        processed = report_data.get("processed_items_count", 0)
        skipped = report_data.get("skipped_posts", [])

        # Format report message
        text = f"📊 <b>Quality Diagnostic Report</b>\n\n"
        text += f"🔍 <b>Query:</b> <code>{query.query}</code>\n"
        text += f"🌐 <b>Total items found on Website:</b> <code>{total_website}</code>\n"
        text += f"📥 <b>Latest posts scanned:</b> <code>{processed}</code>\n"
        text += f"⚠️ <b>Posts missing [480p/720p/1080p]:</b> <code>{len(skipped)}</code>\n\n"

        if skipped:
            text += "<b>Skipped Posts (No matching quality found):</b>\n"
            # Capping display list to fit Telegram message limit (4096 chars)
            max_display = 25
            for idx, title in enumerate(skipped[:max_display], 1):
                text += f"{idx}. {title}\n"
            if len(skipped) > max_display:
                text += f"<i>...and {len(skipped) - max_display} more items.</i>\n"
        else:
            text += "✅ All scanned posts contain at least one of the accepted qualities (480p/720p/1080p)."

        await status_msg.edit_text(text, parse_mode=ParseMode.HTML)

    except Exception as e:
        log.error(f"[Report] Error generating report: {e}")
        await status_msg.edit_text(f"❌ Failed to generate report: {e}")

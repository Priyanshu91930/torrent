"""
admin.py — Admin-only command handlers.
Commands: /broadcast, /blacklist, /unblacklist, /analytics, /clearCache.
"""

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from bot.db.models import db
from bot.utils.cache import torrent_cache
from bot.utils.rate_limiter import rate_limiter
from bot.utils.logger import log
from bot.config import settings


def is_admin(user_id: int) -> bool:
    return user_id in settings.ADMIN_IDS


def admin_only(func):
    """Decorator that blocks non-admin users."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("🚫 Admin only command.")
            return
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper


@admin_only
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Broadcast a message to all users. Usage: /broadcast <message>"""
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return

    text = " ".join(context.args)
    users = await db.get_all_users()
    success = 0
    failed = 0

    status_msg = await update.message.reply_text(
        f"📢 Broadcasting to {len(users)} users…"
    )

    for user in users:
        uid = user["user_id"]
        if user.get("is_banned"):
            continue
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"📢 <b>Broadcast</b>\n\n{text}",
                parse_mode=ParseMode.HTML,
            )
            success += 1
        except Exception:
            failed += 1

    await status_msg.edit_text(
        f"✅ Broadcast complete!\n"
        f"✔ Sent: {success}\n"
        f"✖ Failed: {failed}"
    )


@admin_only
async def blacklist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Blacklist a user. Usage: /blacklist <user_id> [reason]"""
    if not context.args:
        await update.message.reply_text("Usage: /blacklist <user_id> [reason]")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return

    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "No reason provided"
    await db.add_blacklist(target_id, reason)
    rate_limiter.blacklist(target_id)
    log.warning(f"[Admin] User {target_id} blacklisted by {update.effective_user.id}. Reason: {reason}")
    await update.message.reply_text(f"🚫 User <code>{target_id}</code> blacklisted.", parse_mode=ParseMode.HTML)


@admin_only
async def unblacklist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove a user from blacklist. Usage: /unblacklist <user_id>"""
    if not context.args:
        await update.message.reply_text("Usage: /unblacklist <user_id>")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return

    await db.remove_blacklist(target_id)
    rate_limiter.unblacklist(target_id)
    await update.message.reply_text(f"✅ User <code>{target_id}</code> removed from blacklist.", parse_mode=ParseMode.HTML)


@admin_only
async def analytics_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show analytics dashboard."""
    users = await db.get_all_users()
    search_count = await db.get_search_count()
    top_queries = await db.get_top_queries(5)
    blacklist = await db.get_blacklist()

    lines = [
        "📊 <b>Admin Analytics</b>",
        "━━━━━━━━━━━━━━━━",
        f"👥 Total Users: <b>{len(users)}</b>",
        f"🔍 Total Searches: <b>{search_count}</b>",
        f"⚡ Cache Entries: <b>{torrent_cache.size}</b>",
        f"🚫 Blacklisted: <b>{len(blacklist)}</b>",
        "",
        "🔥 <b>Top Queries:</b>",
    ]
    for q in top_queries:
        lines.append(f"  • <code>{q['query']}</code> × {q['cnt']}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@admin_only
async def clear_cache_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear the search cache."""
    count = await torrent_cache.clear()
    await update.message.reply_text(f"🗑 Cleared <b>{count}</b> cache entries.", parse_mode=ParseMode.HTML)


@admin_only
async def list_blacklist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all blacklisted users."""
    blist = await db.get_blacklist()
    if not blist:
        await update.message.reply_text("✅ No blacklisted users.")
        return

    lines = ["🚫 <b>Blacklisted Users</b>", "━━━━━━━━━━━━━━━━"]
    for b in blist:
        from datetime import datetime
        dt = datetime.fromtimestamp(b["banned_at"]).strftime("%Y-%m-%d")
        lines.append(f"• <code>{b['user_id']}</code> — {b['reason']} ({dt})")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

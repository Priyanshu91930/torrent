"""
favorites.py — Handlers for /save, /myfavs, /export commands.
"""

import io
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from bot.db.models import db
from bot.utils.logger import log


async def myfavs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the user's saved favorite torrents."""
    user_id = update.effective_user.id
    favs = await db.get_favorites(user_id)

    if not favs:
        await update.message.reply_text(
            "📭 You have no saved favorites yet.\n\n"
            "Use the <b>⭐ Save</b> button on any result to save it.",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = []
    for i, fav in enumerate(favs, 1):
        size = f" ({fav['size']})" if fav.get("size") else ""
        lines.append(f"<b>{i}.</b> {fav['title']}{size}")

    text = "⭐ <b>Your Saved Torrents</b>\n━━━━━━━━━━━━━━\n" + "\n".join(lines)
    text += f"\n\n<i>Use /export to download all magnets as a file.</i>"

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Export all saved favorite magnet links as a .txt file."""
    user_id = update.effective_user.id
    favs = await db.get_favorites(user_id)

    if not favs:
        await update.message.reply_text(
            "📭 No favorites to export. Save some first!",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = []
    for fav in favs:
        lines.append(f"# {fav['title']}")
        lines.append(fav["magnet"])
        lines.append("")

    content = "\n".join(lines)
    file_obj = io.BytesIO(content.encode("utf-8"))
    file_obj.name = "my_favorites.txt"

    await update.message.reply_document(
        document=file_obj,
        filename="my_favorites.txt",
        caption=(
            f"📤 <b>Exported {len(favs)} magnet(s)</b>\n"
            f"Open any magnet link in your torrent client to start downloading."
        ),
        parse_mode=ParseMode.HTML,
    )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the user's recent search history."""
    user_id = update.effective_user.id
    history = await db.get_user_history(user_id, limit=10)

    if not history:
        await update.message.reply_text("📂 You have no search history yet.")
        return

    import time
    lines = []
    for h in history:
        from datetime import datetime
        dt = datetime.fromtimestamp(h["searched_at"]).strftime("%b %d %H:%M")
        lines.append(f"• <code>{h['query']}</code>  ({h['results']} results)  <i>{dt}</i>")

    text = "📂 <b>Recent Searches</b>\n━━━━━━━━━━━━━━\n" + "\n".join(lines)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

"""
main.py — Bot entry point.
Sets up the Application, registers all handlers, connects the database,
and starts polling.
"""

import asyncio
import logging

from telegram import BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from bot.config import settings
from bot.utils.logger import setup_logger
from bot.db.models import db
from bot.utils.cache import torrent_cache
from bot.utils.userbot import userbot

# Handlers
from bot.handlers.search import search_command, cancel_command
from bot.handlers.pagination import callback_handler
from bot.handlers.info import help_command, stats_command, top_command, latest_command, start_command
from bot.handlers.favorites import myfavs_command, export_command, history_command
from bot.handlers.report import report_command
from bot.handlers.admin import (
    broadcast_command,
    blacklist_command,
    unblacklist_command,
    analytics_command,
    clear_cache_command,
    list_blacklist_command,
)
from bot.handlers.import_txt import import_command, txt_file_message_handler, clear_queue_command, resume_pending_import_jobs, resume_command

log = setup_logger("torrent_bot", settings.LOG_LEVEL, settings.LOG_FILE)


async def post_init(application: Application) -> None:
    """Run after the application is initialized."""
    await db.connect()
    log.info("[OK] Database connected")

    # Resume any unfinished import jobs from before last restart
    await resume_pending_import_jobs()

    if userbot:
        try:
            await userbot.start()
            log.info("[OK] Userbot started for leech features")

            # Scan dialogs to warm the peer cache AND find the leech group
            leech_id = int(settings.LEECH_GROUP_ID) if settings.LEECH_GROUP_ID else None
            leech_found = False

            async for dialog in userbot.get_dialogs(limit=500):
                chat = dialog.chat
                if leech_id and chat.id == leech_id:
                    log.info(f"[OK] Leech group found in dialogs: '{chat.title}' (id={chat.id})")
                    leech_found = True

            log.info("[OK] Userbot peer cache warmed up")

            if leech_id and not leech_found:
                log.error(f"[Userbot] Leech group {leech_id} NOT found in your dialogs!")
                log.error("[Userbot] Open that group in Telegram with your account, then restart.")
            elif leech_id and leech_found:
                log.info("[OK] Leech group is ready for sending!")

                from pyrogram.handlers import MessageHandler as PyrogramMessageHandler
                from pyrogram.handlers import EditedMessageHandler as PyrogramEditedMessageHandler
                from bot.utils.leech_queue import leech_queue

                # Route ALL messages+edits from leech group through handle_message
                # which auto-detects: completion, 100% upload progress, or ignores
                async def handle_leech_new(client, message):
                    chat_id = message.chat.id if message.chat else None
                    text_preview = (message.text or message.caption or '')[:80]
                    log.debug(f"[Queue] 📩 [NEW] from={getattr(message.from_user, 'id', '?')}: {text_preview!r}")
                    if chat_id == leech_id:
                        await leech_queue.handle_message(message)

                async def handle_leech_edit(client, message):
                    chat_id = message.chat.id if message.chat else None
                    text_preview = (message.text or message.caption or '')[:80]
                    log.debug(f"[Queue] ✏️ [EDIT] from={getattr(message.from_user, 'id', '?')}: {text_preview!r}")
                    if chat_id == leech_id:
                        await leech_queue.handle_message(message)

                userbot.add_handler(PyrogramMessageHandler(handle_leech_new), group=-1)
                userbot.add_handler(PyrogramEditedMessageHandler(handle_leech_edit), group=-1)
                log.info(f"[OK] Leech handlers registered (NEW + EDITED, chat_id={leech_id})")

                # Wire admin alert callback so disk-full events reach admins via the bot
                async def _alert_admins(text: str):
                    for admin_id in settings.ADMIN_IDS:
                        try:
                            await application.bot.send_message(admin_id, text, parse_mode="HTML")
                        except Exception as exc:
                            log.error(f"[Queue] Could not alert admin {admin_id}: {exc}")

                leech_queue._admin_alert_fn = _alert_admins
                log.info("[OK] Admin alert callback registered for disk-full events")

        except Exception as e:
            log.error(f"[Userbot] Failed to start userbot: {e}")


    # Set bot commands visible in Telegram menu
    await application.bot.set_my_commands([
        BotCommand("start", "Start the bot"),
        BotCommand("search", "Search torrents: /search <query>"),
        BotCommand("top", "Trending torrents"),
        BotCommand("latest", "Latest uploads"),
        BotCommand("stats", "Bot statistics"),
        BotCommand("myfavs", "Your saved torrents"),
        BotCommand("export", "Export favorite magnets"),
        BotCommand("history", "Your search history"),
        BotCommand("report", "Diagnostic report: website vs bot items"),
        BotCommand("import", "Import links from a txt file"),
        BotCommand("resume", "Resume pending import jobs"),
        BotCommand("clearqueue", "Clear the leech queue"),
        BotCommand("cancel", "Cancel current search"),
        BotCommand("help", "Show all commands"),
    ])
    log.info("[OK] Bot commands registered")


async def post_shutdown(application: Application) -> None:
    """Cleanup on shutdown."""
    await db.close()
    log.info("[BYE] Database connection closed")
    if userbot and userbot.is_connected:
        await userbot.stop()
        log.info("[BYE] Userbot stopped")


def main() -> None:
    if not settings.BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not set! Check your .env file.")

    log.info("[START] Starting Torrent Search Bot")

    app = (
        Application.builder()
        .token(settings.BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # ── User commands ─────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("s", search_command))            # Alias
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("top", top_command))
    app.add_handler(CommandHandler("latest", latest_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("myfavs", myfavs_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("report", report_command))
    app.add_handler(CommandHandler("import", import_command))
    app.add_handler(CommandHandler("resume", resume_command))

    # ── Admin commands ────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("blacklist", blacklist_command))
    app.add_handler(CommandHandler("unblacklist", unblacklist_command))
    app.add_handler(CommandHandler("analytics", analytics_command))
    app.add_handler(CommandHandler("clearcache", clear_cache_command))
    app.add_handler(CommandHandler("blist", list_blacklist_command))
    app.add_handler(CommandHandler("clearqueue", clear_queue_command))
    app.add_handler(CommandHandler("cq", clear_queue_command))

    # /restart — admin only, exits process so Docker restarts the container
    from bot.handlers.admin import admin_only as _admin_only
    import os as _os

    @_admin_only
    async def restart_command(update, context):
        """Restart the bot container (Docker restart:always policy handles the restart)."""
        await update.message.reply_text(
            "🔄 <b>Restarting bot...</b>\nWill be back in a few seconds.",
            parse_mode="HTML"
        )
        log.info(f"[Admin] Restart triggered by user {update.effective_user.id}")
        # Give Telegram time to deliver the message, then exit
        import asyncio as _asyncio
        await _asyncio.sleep(1)
        _os._exit(0)   # Hard exit — Docker restart:always policy will restart the container

    app.add_handler(CommandHandler("restart", restart_command))


    # ── Document / file imports ────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.Document.ALL, txt_file_message_handler))

    # ── Inline keyboard callbacks ─────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(callback_handler))

    # ── Unknown commands ──────────────────────────────────────────────────────
    async def unknown(update, context):
        await update.message.reply_text(
            "❓ Unknown command. Type /help to see available commands."
        )

    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    log.info("[OK] All handlers registered")
    log.info("[BOT] Polling for updates...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

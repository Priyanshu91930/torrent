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
from bot.handlers.import_txt import import_command, txt_file_message_handler, clear_queue_command, resume_pending_import_jobs

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
                
                # Register leech completion listener handler
                from pyrogram.handlers import MessageHandler as PyrogramMessageHandler
                from pyrogram.handlers import EditedMessageHandler as PyrogramEditedMessageHandler
                from bot.utils.leech_queue import leech_queue

                # The leech bot EDITS its initial message when download is done.
                # So we must listen for BOTH new messages AND edited messages.
                async def handle_leech_reply(client, message):
                    chat_id = message.chat.id if message.chat else None
                    sender = message.from_user.id if message.from_user else (message.sender_chat.id if message.sender_chat else 'unknown')
                    text_preview = (message.text or message.caption or '')[:80]
                    log.info(f"[Queue] 📩 Pyrogram msg [NEW]: chat={chat_id} from={sender} text={text_preview!r}")
                    if chat_id == leech_id:
                        await leech_queue.handle_completion(message)

                async def handle_leech_edit(client, message):
                    chat_id = message.chat.id if message.chat else None
                    sender = message.from_user.id if message.from_user else (message.sender_chat.id if message.sender_chat else 'unknown')
                    text_preview = (message.text or message.caption or '')[:80]
                    log.info(f"[Queue] ✏️ Pyrogram msg [EDIT]: chat={chat_id} from={sender} text={text_preview!r}")
                    if chat_id == leech_id:
                        await leech_queue.handle_completion(message)

                # Listen for new messages
                userbot.add_handler(
                    PyrogramMessageHandler(handle_leech_reply),
                    group=-1
                )
                # Also listen for EDITED messages — leech bots edit their status messages
                userbot.add_handler(
                    PyrogramEditedMessageHandler(handle_leech_edit),
                    group=-1
                )
                log.info(f"[OK] Registered leech completion listener (NEW + EDITED messages, chat_id={leech_id})")

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

    # ── Admin commands ────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("blacklist", blacklist_command))
    app.add_handler(CommandHandler("unblacklist", unblacklist_command))
    app.add_handler(CommandHandler("analytics", analytics_command))
    app.add_handler(CommandHandler("clearcache", clear_cache_command))
    app.add_handler(CommandHandler("blist", list_blacklist_command))
    app.add_handler(CommandHandler("clearqueue", clear_queue_command))
    app.add_handler(CommandHandler("cq", clear_queue_command))

    # /skiplink — manually release the oldest stuck active slot
    from bot.handlers.admin import admin_only as _admin_only
    from bot.utils.leech_queue import leech_queue as _leech_queue

    @_admin_only
    async def skiplink_command(update, context):
        """Manually release the oldest active queue slot (for stuck/corrupt links)."""
        async with _leech_queue.lock:
            if not _leech_queue.active:
                await update.message.reply_text("ℹ️ No active tasks in queue.")
                return
            oldest_msg_id = min(_leech_queue.active.keys())
            magnet, idx, user_id, query_key, session, start_time = _leech_queue.active.pop(oldest_msg_id)

        import asyncio
        asyncio.create_task(_leech_queue._process_queue())
        await update.message.reply_text(
            f"⏭️ Skipped task <b>#{idx}</b> (msg_id={oldest_msg_id}).\n"
            f"Next link will be sent shortly.",
            parse_mode="HTML"
        )

    app.add_handler(CommandHandler("skiplink", skiplink_command))
    app.add_handler(CommandHandler("sl", skiplink_command))


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

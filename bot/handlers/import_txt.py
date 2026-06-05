import io
import hashlib
from telegram import Update
from telegram.ext import ContextTypes
from bot.handlers.admin import admin_only
from bot.utils.leech_queue import leech_queue
from bot.utils.logger import log
from bot.db.models import db
from bot.config import settings


@admin_only
async def clear_queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear the leech queue."""
    count = await leech_queue.clear_queue()
    await update.message.reply_text(f"🗑️ Cleared leech queue. Released/canceled <b>{count}</b> items.", parse_mode="HTML")


@admin_only
async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Resume the pending import jobs."""
    user_id = update.effective_user.id
    status = await do_resume_jobs(user_id)
    await update.message.reply_text(status, parse_mode="HTML")


async def _queue_links(user_id: int, query_key: str, links: list) -> None:
    """Add links to the queue, skipping already-sent ones from DB."""
    db_sent = await db.get_leech_sent(user_id, query_key)
    session = {
        "results": [{"magnet": link, "title": f"Link {i}"} for i, link in enumerate(links, 1)],
        "sent_magnets": set(db_sent),
        "query": None,
        "query_key": query_key,
    }
    pending = [l for l in links if l not in db_sent]
    log.info(f"[Import] Resuming job {query_key}: {len(pending)} pending / {len(db_sent)} already sent")
    if pending:
        await leech_queue.add_to_queue(links, user_id, query_key, session)


async def process_txt_file(update: Update, context: ContextTypes.DEFAULT_TYPE, document) -> None:
    """Download and parse a .txt file containing links, save checkpoint, then queue."""
    user = update.effective_user
    user_id = user.id

    if user_id not in settings.ADMIN_IDS:
        await update.message.reply_text("🚫 You are not authorized to use the import feature.")
        return

    from bot.utils.userbot import userbot
    if not userbot or not userbot.is_connected:
        await update.message.reply_text("❌ Userbot is not running or not configured.")
        return

    status_msg = await update.message.reply_text("📥 Downloading text file...")

    try:
        file_io = io.BytesIO()
        file = await context.bot.get_file(document.file_id)
        await file.download_to_memory(out=file_io)
        file_io.seek(0)

        content = file_io.read().decode('utf-8', errors='ignore')
        lines = [line.strip() for line in content.split('\n') if line.strip()]

        links = [
            line for line in lines
            if line.startswith("magnet:") or line.startswith("http://") or line.startswith("https://")
        ]

        if not links:
            await status_msg.edit_text("❌ No valid links found in the file.")
            return

        # Use a stable hash of the file content as the query_key
        # This ensures the same file always uses the same checkpoint in the DB
        content_hash = hashlib.md5(content.encode()).hexdigest()[:12]
        query_key = f"{user_id}:import_{content_hash}"

        # Check if this exact job already has progress
        db_sent = await db.get_leech_sent(user_id, query_key)
        already_sent = len(db_sent)
        pending = len(links) - already_sent

        # Save the full job to DB (checkpoint)
        await db.save_import_job(user_id, query_key, links)

        if already_sent > 0:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("▶️ Resume from Left", callback_data=f"import_res:{query_key}"),
                    InlineKeyboardButton("🔄 Start New", callback_data=f"import_restart:{query_key}")
                ]
            ])
            await status_msg.edit_text(
                f"⚠️ <b>Existing Progress Found</b>\n"
                f"📋 Total links: <b>{len(links)}</b>\n"
                f"✔️ Already sent: <b>{already_sent}</b>\n"
                f"⏳ Remaining: <b>{pending}</b>\n\n"
                f"Would you like to resume from where it left off, or start a new job (which clears previous progress)?",
                parse_mode="HTML",
                reply_markup=keyboard
            )
        else:
            await status_msg.edit_text(
                f"✅ <b>Import Job Loaded</b>\n"
                f"📋 Total links: <b>{len(links)}</b>\n"
                f"✔️ Already sent: <b>{already_sent}</b>\n"
                f"⏳ Queuing now: <b>{pending}</b>\n"
                f"🔑 Job key: <code>{query_key}</code>",
                parse_mode="HTML"
            )
            await _queue_links(user_id, query_key, links)

    except Exception as e:
        log.error(f"[Import] Error processing text file: {e}")
        await status_msg.edit_text(f"❌ Failed to process text file: {e}")


async def resume_pending_import_jobs(bot=None) -> None:
    """Called on bot startup — notifies admin about unfinished jobs WITHOUT auto-starting.
    
    We deliberately do NOT auto-send on restart because the leech bot may still
    have the old tasks running, causing 'tasks limit exceeded' errors.
    The admin must send /resume to manually continue.
    """
    try:
        jobs = await db.get_pending_import_jobs()
        if not jobs:
            log.info("[Import] No pending import jobs to resume.")
            return

        import json
        total_pending = 0
        job_lines = []
        for job in jobs:
            user_id = job["user_id"]
            query_key = job["query_key"]
            links = json.loads(job["links_json"])
            db_sent = await db.get_leech_sent(user_id, query_key)
            pending_count = len(links) - len(db_sent)
            total_pending += pending_count
            job_lines.append(
                f"• <code>{query_key}</code>: {len(db_sent)}/{len(links)} sent, "
                f"<b>{pending_count} remaining</b>"
            )

        log.info(f"[Import] Found {len(jobs)} pending job(s) with {total_pending} total links. Waiting for /resume.")

        # Alert all admins — do NOT start the queue automatically
        if bot and total_pending > 0:
            msg = (
                f"🔄 <b>Bot restarted with pending import jobs!</b>\n\n"
                f"{chr(10).join(job_lines)}\n\n"
                f"⚠️ Queue is <b>NOT</b> auto-started to avoid conflicts with\n"
                f"tasks still running in the leech group.\n\n"
                f"Send /resume when you're ready to continue."
            )
            for admin_id in settings.ADMIN_IDS:
                try:
                    await bot.send_message(admin_id, msg, parse_mode="HTML")
                except Exception as e:
                    log.error(f"[Import] Could not alert admin {admin_id}: {e}")

    except Exception as e:
        log.error(f"[Import] Error checking pending import jobs: {e}")


async def do_resume_jobs(user_id: int) -> str:
    """Actually load and queue all pending import jobs. Called by /resume command."""
    try:
        jobs = await db.get_pending_import_jobs()
        if not jobs:
            return "ℹ️ No pending import jobs found."

        import json
        total_queued = 0
        for job in jobs:
            job_user_id = job["user_id"]
            query_key = job["query_key"]
            links = json.loads(job["links_json"])
            db_sent = await db.get_leech_sent(job_user_id, query_key)
            pending = [l for l in links if l not in db_sent]
            if pending:
                session = {
                    "results": [{"magnet": l, "title": f"Link {i}"} for i, l in enumerate(links, 1)],
                    "sent_magnets": set(db_sent),
                    "query": None,
                    "query_key": query_key,
                }
                await leech_queue.add_to_queue(links, job_user_id, query_key, session)
                total_queued += len(pending)
                log.info(f"[Import] Resumed job {query_key}: {len(pending)} links queued")

        if total_queued:
            return f"▶️ Resumed! <b>{total_queued}</b> links added to queue."
        return "ℹ️ All jobs already completed — nothing to resume."

    except Exception as e:
        log.error(f"[Import] Error in do_resume_jobs: {e}")
        return f"❌ Error resuming: {e}"



@admin_only
async def import_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /import when replied to a text file."""
    message = update.message
    if not message.reply_to_message or not message.reply_to_message.document:
        await message.reply_text("❓ Please reply to a <code>.txt</code> file message with /import", parse_mode="HTML")
        return

    document = message.reply_to_message.document
    if not document.file_name.endswith('.txt'):
        await message.reply_text("❌ Replying file must be a <code>.txt</code> text file.", parse_mode="HTML")
        return

    await process_txt_file(update, context, document)


async def txt_file_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Direct handler when a user uploads a .txt file."""
    message = update.message
    if not message.document or not message.document.file_name.endswith('.txt'):
        return
    await process_txt_file(update, context, message.document)

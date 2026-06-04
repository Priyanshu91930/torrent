import io
import time
from telegram import Update
from telegram.ext import ContextTypes
from bot.handlers.admin import admin_only
from bot.utils.leech_queue import leech_queue
from bot.utils.logger import log
from bot.config import settings

@admin_only
async def clear_queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear the leech queue."""
    count = await leech_queue.clear_queue()
    await update.message.reply_text(f"🗑️ Cleared leech queue. Released/canceled <b>{count}</b> items.", parse_mode="HTML")

async def process_txt_file(update: Update, context: ContextTypes.DEFAULT_TYPE, document) -> None:
    """Helper to download and parse a txt file containing links, then queue them for leeching."""
    user = update.effective_user
    user_id = user.id
    
    if user_id not in settings.ADMIN_IDS:
        await update.message.reply_text("🚫 You are not authorized to use the import feature.")
        return

    # Must have userbot configured
    from bot.utils.userbot import userbot
    if not userbot or not userbot.is_connected:
        await update.message.reply_text("❌ Userbot is not running or not configured.")
        return

    status_msg = await update.message.reply_text("📥 Downloading text file...")
    
    try:
        # Download file to memory
        file_io = io.BytesIO()
        file = await context.bot.get_file(document.file_id)
        await file.download_to_memory(out=file_io)
        file_io.seek(0)
        
        content = file_io.read().decode('utf-8', errors='ignore')
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        
        # Extract links/magnets
        links = []
        for line in lines:
            if line.startswith("magnet:") or line.startswith("http://") or line.startswith("https://"):
                links.append(line)
        
        if not links:
            await status_msg.edit_text("❌ No valid links (starting with magnet:, http:, or https:) found in the file.")
            return
            
        await status_msg.edit_text(f"✅ Found {len(links)} links. Adding them to leech queue...")
        
        query_key = f"{user_id}:import_{int(time.time())}"
        session = {
            "results": [{"magnet": link, "title": f"Imported Link {i}"} for i, link in enumerate(links, 1)],
            "sent_magnets": set(),
            "query": None
        }
        
        await leech_queue.add_to_queue(links, user_id, query_key, session)
        
    except Exception as e:
        log.error(f"[Import] Error processing text file: {e}")
        await status_msg.edit_text(f"❌ Failed to process text file: {e}")

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
    """Direct message handler when a user uploads a .txt file."""
    message = update.message
    if not message.document or not message.document.file_name.endswith('.txt'):
        return
        
    await process_txt_file(update, context, message.document)

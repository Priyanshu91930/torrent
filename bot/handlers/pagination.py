"""
pagination.py — Inline keyboard callback handler for Next/Prev/Copy/Save/Export.
"""

import io
import asyncio
from typing import List, Set

from pyrogram.errors import FloodWait

from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from bot.handlers.search import _sessions, build_keyboard, format_result, format_magnet_block
from bot.utils.formatter import format_result, format_magnet_block
from bot.db.models import db
from bot.utils.logger import log
from bot.config import settings
from bot.utils.userbot import userbot
from bot.utils.leech_queue import leech_queue



def _build_leech_cmd(link: str, title: str = None) -> str:
    """Build the leech bot command, appending -e flag for zip/rar archives or series/packs."""
    cmd = f"/l {link}"
    # Strip query parameters (e.g. ?token=abc) before checking file extension
    link_path = link.split("?")[0].lower()
    
    is_archive = link_path.endswith((".zip", ".rar", ".7z"))
    is_series = False
    
    if title:
        title_lower = title.lower()
        import re
        if "season" in title_lower or "complete" in title_lower or "pack" in title_lower or "episodes" in title_lower:
            is_series = True
        elif re.search(r"\bs\d+", title_lower):
            is_series = True
        elif re.search(r"ep(?:isodes?|\.)?\s*(\d+)\s*(?:to|-)\s*(\d+)", title_lower):
            is_series = True
        elif re.search(r"e(\d+)\s*(?:to|-)\s*e?(\d+)", title_lower):
            is_series = True

    if is_archive or is_series:
        cmd += " -e"
    return cmd


async def _send_all_magnets_task(
    magnets: List[str], group_id, sent_set: Set[str], session: dict,
    user_id: int, query_key: str
):
    """Background task to send magnets sequentially, skipping already-sent ones.
    Persists every sent link to DB so progress survives bot restarts.
    """
    total = len(magnets)
    sent_count = 0

    for idx, magnet in enumerate(magnets, start=1):
        # Skip links already sent (loaded from DB or sent this run)
        if magnet in sent_set:
            log.info(f"[Userbot] [{idx}/{total}] Skipping already-sent link")
            continue
        try:
            log.info(f"[Userbot] [{idx}/{total}] Sending link #{idx}")
            title = None
            if session and "results" in session:
                for r in session["results"]:
                    r_magnet = r.get("magnet") if isinstance(r, dict) else getattr(r, "magnet", None)
                    if r_magnet == magnet:
                        title = r.get("title") if isinstance(r, dict) else getattr(r, "title", None)
                        break
            await userbot.send_message(group_id, _build_leech_cmd(magnet, title))
            sent_set.add(magnet)
            sent_count += 1
            # ⭐ Persist to DB immediately so restart won't re-send this
            await db.mark_leech_sent(user_id, query_key, magnet, idx)
            session["leech_progress"] = {"sent": len(sent_set), "total": total, "last_idx": idx}
            await asyncio.sleep(10)
        except FloodWait as e:
            wait_time = e.value + 2
            log.warning(f"[Userbot] [{idx}/{total}] FloodWait: sleeping {wait_time}s as required by Telegram")
            await asyncio.sleep(wait_time)
            try:
                await userbot.send_message(group_id, _build_leech_cmd(magnet, title))
                sent_set.add(magnet)
                sent_count += 1
                await db.mark_leech_sent(user_id, query_key, magnet, idx)
                session["leech_progress"] = {"sent": len(sent_set), "total": total, "last_idx": idx}
                await asyncio.sleep(10)
            except Exception as retry_e:
                log.error(f"[Userbot] [{idx}/{total}] Retry failed after FloodWait: {retry_e}")
        except Exception as e:
            log.error(f"[Userbot] Stopped at link #{idx}/{total} — Error: {e}")
            session["leech_progress"] = {"sent": len(sent_set), "total": total, "last_idx": idx, "stopped": True}
            break

    log.info(f"[Userbot] Batch leech finished: {sent_count} new links sent out of {total} total")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all inline keyboard callbacks."""
    query = update.callback_query
    await query.answer()  # Acknowledge immediately to remove loading spinner

    data: str = query.data or ""
    user_id = update.effective_user.id

    # ── Navigation ────────────────────────────────────────────────────────────
    if data.startswith("page:"):
        _, direction, uid = data.split(":")
        uid = int(uid)
        if uid != user_id:
            await query.answer("❌ This is not your search session.", show_alert=True)
            return

        session = _sessions.get(user_id)
        if not session:
            await query.answer("Session expired. Please search again.", show_alert=True)
            return

        results = session["results"]
        page = session["page"]
        total = len(results)

        if direction == "next" and page < total - 1:
            session["page"] += 1
        elif direction == "prev" and page > 0:
            session["page"] -= 1
        else:
            await query.answer("No more results in that direction.")
            return

        page = session["page"]
        result = results[page]

        text = format_result(result, page + 1, total)
        magnet_block = format_magnet_block(result.magnet)
        full_text = text + "\n\n" + magnet_block
        keyboard = build_keyboard(user_id)

        try:
            await query.edit_message_text(
                full_text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except Exception as e:
            log.debug(f"[Pagination] edit_message_text error: {e}")

    elif data.startswith("copy:"):
        uid = int(data.split(":")[1])
        if uid != user_id:
            return

        session = _sessions.get(user_id)
        if not session:
            await query.answer("Session expired.", show_alert=True)
            return

        result = session["results"][session["page"]]
        if result.magnet:
            is_http = result.magnet.startswith("http")
            label = "Direct Download Link" if is_http else "Magnet Link"
            await query.message.reply_text(
                f"📋 <b>{label}:</b>\n<code>{result.magnet}</code>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await query.answer("No link available.", show_alert=True)

    # ── Save favorite ─────────────────────────────────────────────────────────
    elif data.startswith("save:"):
        uid = int(data.split(":")[1])
        if uid != user_id:
            return

        session = _sessions.get(user_id)
        if not session:
            await query.answer("Session expired.", show_alert=True)
            return

        result = session["results"][session["page"]]
        if result.magnet:
            await db.save_favorite(
                user_id=user_id,
                title=result.title,
                magnet=result.magnet,
                size=result.size or "",
            )
            await query.answer("⭐ Saved to favorites!", show_alert=True)
        else:
            await query.answer("No magnet to save.", show_alert=True)

    # ── Export all magnets ────────────────────────────────────────────────────
    elif data.startswith("export:"):
        uid = int(data.split(":")[1])
        if uid != user_id:
            return

        session = _sessions.get(user_id)
        if not session:
            await query.answer("Session expired.", show_alert=True)
            return

        results = session["results"]
        magnets = [r.magnet for r in results if r.magnet]

        if not magnets:
            await query.answer("No magnets to export.", show_alert=True)
            return

        content = "\n\n".join(
            f"# {results[i].title}\n{results[i].magnet}"
            for i, r in enumerate(results)
            if r.magnet
        )
        file_obj = io.BytesIO(content.encode())
        file_obj.name = "magnets.txt"

        await query.message.reply_document(
            document=file_obj,
            filename="magnets.txt",
            caption=f"📤 Exported <b>{len(magnets)}</b> magnet link(s)",
            parse_mode=ParseMode.HTML,
        )
        await query.answer("Exported!", show_alert=False)

    # ── Send to Leech Group ───────────────────────────────────────────────────
    elif data.startswith("leech:"):
        uid = int(data.split(":")[1])
        if uid != user_id:
            return

        session = _sessions.get(user_id)
        if not session:
            await query.answer("Session expired.", show_alert=True)
            return

        result = session["results"][session["page"]]
        if not result.magnet:
            await query.answer("No magnet link available.", show_alert=True)
            return

        if not userbot or not userbot.is_connected:
            await query.answer("Userbot is not running or not configured.", show_alert=True)
            return

        try:
            await userbot.send_message(settings.LEECH_GROUP_ID, _build_leech_cmd(result.magnet, result.title))
            await query.answer("📥 Sent to Leech Group!", show_alert=True)
        except Exception as e:
            log.error(f"[Userbot] Error sending to leech group: {e}")
            await query.answer("❌ Failed to send. Check group ID and permissions.", show_alert=True)

    # ── Leech All Magnets ─────────────────────────────────────────────────────
    elif data.startswith("leechall:"):
        uid = int(data.split(":")[1])
        if uid != user_id:
            return

        session = _sessions.get(user_id)
        if not session:
            await query.answer("Session expired.", show_alert=True)
            return

        magnets = [r.magnet for r in session["results"] if r.magnet]
        if not magnets:
            await query.answer("No magnets found to send.", show_alert=True)
            return

        if not userbot or not userbot.is_connected:
            await query.answer("Userbot is not running.", show_alert=True)
            return

        # Build a query key from user_id + search query for DB tracking
        search_query = session.get("query")
        query_key = f"{user_id}:{search_query.raw if search_query else 'unknown'}"

        # Load already-sent links from DB (survives restarts)
        db_sent = await db.get_leech_sent(user_id, query_key)
        prog_db = await db.get_leech_progress(user_id, query_key)

        # Merge DB sent with in-memory sent_set
        if "sent_magnets" not in session:
            session["sent_magnets"] = set()
        session["sent_magnets"].update(db_sent)  # Load from DB into memory
        sent_set = session["sent_magnets"]

        pending = [m for m in magnets if m not in sent_set]
        already_sent = len(magnets) - len(pending)

        if not pending:
            await query.answer(
                f"✅ All {len(magnets)} links already sent! Search again to get new results.",
                show_alert=True
            )
            return

        resume_msg = f" | Resuming from #{prog_db['last_idx']+1}" if prog_db['last_idx'] else ""

        # Add to the limit-5 leech queue manager
        await leech_queue.add_to_queue(magnets, user_id, query_key, session)

        await query.answer(
            f"📥 Queueing {len(pending)} links | {already_sent} already sent{resume_msg}",
            show_alert=True
        )

    # ── Import Job Resume / Restart ───────────────────────────────────────────
    elif data.startswith("import_res:") or data.startswith("import_restart:"):
        is_restart = data.startswith("import_restart:")
        query_key = data.split(":", 1)[1]

        job = await db.get_import_job(query_key)
        if not job:
            await query.answer("❌ Import job not found in database.", show_alert=True)
            return

        import json
        links = json.loads(job["links_json"])

        if is_restart:
            await db.clear_leech_sent(user_id, query_key)
            # resets completed status in DB
            await db.save_import_job(user_id, query_key, links)
            msg_text = "🔄 Started new import job (cleared previous progress)."
        else:
            msg_text = "▶️ Resuming import job from left."

        await query.message.edit_text(
            f"✅ <b>Import Job Acknowledged</b>\n\n"
            f"{msg_text}\n"
            f"📋 Total links: <b>{len(links)}</b>\n"
            f"⏳ Queuing links now...",
            parse_mode=ParseMode.HTML
        )

        from bot.handlers.import_txt import _queue_links
        await _queue_links(user_id, query_key, links)
        await query.answer("Job queued!", show_alert=False)

    # ── No-op (page counter display) ──────────────────────────────────────────
    elif data == "noop":
        pass

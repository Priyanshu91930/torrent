import asyncio
import re
import datetime
from bot.utils.logger import log
from bot.db.models import db
from bot.utils.userbot import userbot
from bot.config import settings

def refresh_fsl_link(url: str) -> str:
    """Strip any old dynamic token suffixes and append current minute token."""
    if not url:
        return url
    
    # Check if this is an FSL / FSLv2 link
    if "hub.homelander.buzz" in url or "fsl." in url or "cdn." in url:
        url = re.sub(r'(_1\d+|1\d+)$', '', url)
        minutes = datetime.datetime.now().minute
        if "cdn." in url:
            url += f'_1{minutes}'
        else:
            url += f'1{minutes}'
        log.info(f"[Queue] Refreshed token for FSL link (minute={minutes})")
    return url

def is_completion_message(message) -> bool:
    """Check if the message is a genuine completion or stopped notification."""
    text = (message.text or message.caption or "").lower()
    if not text:
        return False
        
    # Standard indicators for finished leech downloads in leech groups
    completion_keywords = [
        "time taken", "download stopped", "stopped!", "completed", 
        "done", "uploaded", "successfully uploaded", "size:", 
        "total size:", "speed:", "index link:", "direct link:", "seeders:"
    ]
    if any(kw in text for kw in completion_keywords):
        return True
    return False

class LeechQueueManager:
    def __init__(self):
        self.pending = []  # items: (magnet, idx, user_id, query_key, session)
        self.active = {}   # msg_id -> (magnet, idx, user_id, query_key, session, start_time)
        self.lock = asyncio.Lock()
        self.is_sending = False

    async def send_status_log(self, user_id: int, text: str):
        """Send status updates directly to the user's Telegram PM."""
        try:
            if userbot and userbot.is_connected:
                await userbot.send_message(user_id, f"🤖 <b>[Leech Log]</b>\n{text}", parse_mode=None)
        except Exception as e:
            log.error(f"[Queue] Failed to send status log to user {user_id}: {e}")

    async def clear_queue(self) -> int:
        """Clear all active and pending items in the queue."""
        async with self.lock:
            pending_count = len(self.pending)
            active_count = len(self.active)
            self.pending.clear()
            self.active.clear()
            log.info(f"[Queue] Cleared queue. Released {pending_count} pending and {active_count} active items.")
            return pending_count + active_count

    async def add_to_queue(self, magnets, user_id, query_key, session):
        async with self.lock:
            if "sent_magnets" not in session:
                session["sent_magnets"] = set()
            sent_set = session["sent_magnets"]
            
            added_count = 0
            for idx, magnet in enumerate(magnets, start=1):
                if magnet not in sent_set:
                    self.pending.append((magnet, idx, user_id, query_key, session))
                    added_count += 1
            
            log.info(f"[Queue] Added {added_count} links to queue. Pending: {len(self.pending)}")
            await self.send_status_log(user_id, f"📥 Added {added_count} links to leech queue. Total pending: {len(self.pending)}")
            
            # Trigger sending the initial batch of up to 5
            asyncio.create_task(self._process_queue())

    async def _process_queue(self):
        async with self.lock:
            if self.is_sending:
                return
            self.is_sending = True

        try:
            while True:
                async with self.lock:
                    # Clean up timed-out active tasks (e.g., active for > 15 minutes)
                    now = datetime.datetime.now()
                    timed_out_ids = []
                    for msg_id, val in list(self.active.items()):
                        start_time = val[5] if len(val) > 5 else now
                        if (now - start_time).total_seconds() > 900: # 15 minutes
                            timed_out_ids.append(msg_id)
                    
                    for msg_id in timed_out_ids:
                        magnet, idx, user_id, query_key, session, _ = self.active.pop(msg_id)
                        log.warning(f"[Queue] Task #{idx} timed out after 15 minutes. Removing from active.")
                        await self.send_status_log(user_id, f"⚠️ Task #{idx} timed out (15 mins) and was auto-released to continue the queue.")

                    if len(self.active) >= 5:
                        log.info(f"[Queue] Active limit (5) reached. Active messages: {list(self.active.keys())}")
                        break
                    if not self.pending:
                        log.info("[Queue] No more pending links in queue.")
                        break
                    # Get next item
                    magnet, idx, user_id, query_key, session = self.pending.pop(0)

                # Send this item
                try:
                    refreshed_magnet = refresh_fsl_link(magnet)
                    
                    log.info(f"[Queue] [{idx}] Sending refreshed link to leech group")
                    await self.send_status_log(user_id, f"🚀 Sending task #{idx} to leech group...\nLink: {refreshed_magnet}")
                    
                    cmd = f"/l {refreshed_magnet}"
                    if refreshed_magnet.lower().endswith((".zip", ".rar")):
                        cmd += " -e"

                    msg = await userbot.send_message(settings.LEECH_GROUP_ID, cmd)
                    
                    async with self.lock:
                        self.active[msg.id] = (magnet, idx, user_id, query_key, session, datetime.datetime.now())
                        session["sent_magnets"].add(magnet)

                    await db.mark_leech_sent(user_id, query_key, magnet, idx)
                    session["leech_progress"] = {
                        "sent": len(session["sent_magnets"]),
                        "total": len(session["results"]),
                        "last_idx": idx
                    }
                    
                    # Sleep 10s between sending to prevent Telegram FloodWait
                    await asyncio.sleep(10)
                except Exception as e:
                    log.error(f"[Queue] Error sending link #{idx}: {e}")
                    await self.send_status_log(user_id, f"⚠️ Error sending link #{idx}: {e}")
                    await asyncio.sleep(2)
        finally:
            async with self.lock:
                self.is_sending = False

    async def handle_completion(self, reply_msg):
        target_id = None
        if reply_msg.reply_to_message:
            target_id = reply_msg.reply_to_message.id
        elif hasattr(reply_msg, "reply_to_message_id") and reply_msg.reply_to_message_id:
            target_id = reply_msg.reply_to_message_id

        if not target_id:
            return

        async with self.lock:
            if target_id not in self.active:
                return

        # Make sure it's a real completed or stopped notification
        if not is_completion_message(reply_msg):
            return

        async with self.lock:
            if target_id not in self.active:
                return
            magnet, idx, user_id, query_key, session, _ = self.active.pop(target_id)
            
            text = (reply_msg.text or reply_msg.caption or "").lower()
            if "stopped" in text or "stopped!" in text:
                log.info(f"[Queue] Task #{idx} stopped/failed.")
                await self.send_status_log(user_id, f"❌ Task #{idx} was stopped or failed by leech bot!")
            else:
                log.info(f"[Queue] Task #{idx} completed.")
                await self.send_status_log(user_id, f"✅ Task #{idx} completed successfully!")
            
            # Trigger processing next in queue
            asyncio.create_task(self._process_queue())

leech_queue = LeechQueueManager()

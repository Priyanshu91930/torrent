import asyncio
import re
import datetime
from bot.utils.logger import log
from bot.db.models import db
from bot.utils.userbot import userbot
from bot.config import settings

# How long to wait after seeing "100%" before auto-skipping (seconds)
STUCK_AFTER_UPLOAD_TIMEOUT = 180  # 3 minutes

# Hard cap: if a task hasn't completed within this many seconds, auto-skip regardless
HARD_TIMEOUT_SECONDS = 3600  # 60 minutes


def refresh_fsl_link(url: str) -> str:
    """Strip any old dynamic token suffixes and append current minute token."""
    if not url:
        return url
    if "hub.homelander.buzz" in url or "fsl." in url or "cdn." in url:
        url = re.sub(r'(_1\d+|1\d+)$', '', url)
        minutes = datetime.datetime.now().minute
        if "cdn." in url:
            url += f'_1{minutes}'
        else:
            url += f'1{minutes}'
        log.info(f"[Queue] Refreshed token for FSL link (minute={minutes})")
    return url


def is_completion_message(text: str) -> bool:
    """Return True if text indicates a finished (or failed) leech download."""
    if not text:
        return False
    t = text.lower()
    keywords = [
        "time taken", "download stopped", "stopped!", "completed",
        "successfully uploaded", "elapsed:", "total files:",
        "sent to bot pm", "have been sent", "index link:",
    ]
    return any(kw in t for kw in keywords)


def is_disk_full_message(text: str) -> bool:
    """Return True if the leech bot failed because the server disk is full."""
    if not text:
        return False
    t = text.lower()
    return (
        "no space left on device" in t
        or "fallocate failed" in t
        or "not enough space" in t
        or "disk full" in t
    )


def is_upload_complete_message(text: str) -> bool:
    """Return True if text shows the file is at 100% upload progress (but not yet sent to PM)."""
    if not text:
        return False
    t = text.lower()
    # Leech bots often show "100%" during the upload phase before the final completion edit
    return "100%" in t and ("upload" in t or "leech" in t or "progress" in t or "%" in t)


class ActiveTask:
    """Holds all state for one active leech slot."""
    __slots__ = (
        "magnet", "idx", "user_id", "query_key", "session",
        "start_time", "upload_done_time",
    )

    def __init__(self, magnet, idx, user_id, query_key, session):
        self.magnet = magnet
        self.idx = idx
        self.user_id = user_id
        self.query_key = query_key
        self.session = session
        self.start_time = datetime.datetime.now()
        self.upload_done_time: datetime.datetime | None = None  # set when 100% seen

    def mark_upload_done(self):
        if self.upload_done_time is None:
            self.upload_done_time = datetime.datetime.now()
            log.info(
                f"[Queue] Task #{self.idx}: upload reached 100%. "
                f"Will auto-skip in {STUCK_AFTER_UPLOAD_TIMEOUT//60} min if no completion."
            )

    def is_stuck(self) -> bool:
        """Return True if this task should be auto-skipped."""
        now = datetime.datetime.now()
        # Case 1: upload hit 100% but no completion in 10 minutes
        if self.upload_done_time:
            if (now - self.upload_done_time).total_seconds() > STUCK_AFTER_UPLOAD_TIMEOUT:
                return True
        # Case 2: hard cap — running for more than 60 minutes total
        if (now - self.start_time).total_seconds() > HARD_TIMEOUT_SECONDS:
            return True
        return False

    def stuck_reason(self) -> str:
        if self.upload_done_time:
            return f"stuck at 100% upload for >{STUCK_AFTER_UPLOAD_TIMEOUT//60} min"
        return f"hard timeout >{HARD_TIMEOUT_SECONDS//60} min"


class LeechQueueManager:
    def __init__(self):
        self.pending: list = []         # (magnet, idx, user_id, query_key, session)
        self.active: dict[int, ActiveTask] = {}  # msg_id → ActiveTask
        self.lock = asyncio.Lock()
        self.is_sending = False
        self.paused = False             # True when disk is full — stops sending new tasks
        # Background watchdog task reference
        self._watchdog_task: asyncio.Task | None = None
        # Admin alert callback — set by main.py after bot starts
        self._admin_alert_fn = None  # async fn(text: str)

    # ── Watchdog ──────────────────────────────────────────────────────────────

    def start_watchdog(self):
        """Start the background watchdog that auto-skips stuck tasks."""
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())
            log.info("[Queue] Watchdog started.")

    async def _watchdog_loop(self):
        """Every 60 seconds, check for stuck tasks and auto-skip them."""
        while True:
            await asyncio.sleep(60)
            await self._auto_skip_stuck()

    async def _auto_skip_stuck(self):
        """Find and release any stuck active tasks, then kick the queue."""
        skipped = []
        async with self.lock:
            for msg_id, task in list(self.active.items()):
                if task.is_stuck():
                    self.active.pop(msg_id)
                    skipped.append(task)

        for task in skipped:
            reason = task.stuck_reason()
            log.warning(
                f"[Queue] ⏭️ Auto-skipping task #{task.idx} ({reason}). "
                f"Sending next link..."
            )
            await self.send_status_log(
                task.user_id,
                f"⏭️ Task #{task.idx} auto-skipped ({reason}). Moving to next link."
            )

        if skipped:
            asyncio.create_task(self._process_queue())

    # ── Status log ────────────────────────────────────────────────────────────

    async def send_status_log(self, user_id: int, text: str):
        """Send status updates directly to the user's Telegram PM."""
        try:
            if userbot and userbot.is_connected:
                await userbot.send_message(user_id, f"🤖 <b>[Leech Log]</b>\n{text}", parse_mode=None)
        except Exception as e:
            log.error(f"[Queue] Failed to send status log to user {user_id}: {e}")

    # ── Queue management ──────────────────────────────────────────────────────

    async def clear_queue(self) -> int:
        async with self.lock:
            count = len(self.pending) + len(self.active)
            self.pending.clear()
            self.active.clear()
            log.info(f"[Queue] Cleared queue ({count} items).")
            return count

    async def alert_admins(self, text: str):
        """Send an urgent alert to all admins."""
        if self._admin_alert_fn:
            try:
                await self._admin_alert_fn(text)
            except Exception as e:
                log.error(f"[Queue] Failed to alert admins: {e}")

    async def add_to_queue(self, magnets, user_id, query_key, session):
        async with self.lock:
            if "sent_magnets" not in session:
                session["sent_magnets"] = set()
            sent_set = session["sent_magnets"]

            added = 0
            for idx, magnet in enumerate(magnets, start=1):
                if magnet not in sent_set:
                    self.pending.append((magnet, idx, user_id, query_key, session))
                    added += 1

            log.info(f"[Queue] Added {added} links. Pending: {len(self.pending)}")
            await self.send_status_log(
                user_id,
                f"📥 Added {added} links to leech queue. Total pending: {len(self.pending)}"
            )

        self.paused = False  # resume if it was paused before
        self.start_watchdog()
        asyncio.create_task(self._process_queue())

    async def _process_queue(self):
        async with self.lock:
            if self.is_sending:
                return
            self.is_sending = True

        try:
            while True:
                async with self.lock:
                    if self.paused:
                        log.info("[Queue] Queue is PAUSED (disk full). Stopping send loop.")
                        break
                    active_count = len(self.active)
                    if active_count >= 5:
                        log.info(f"[Queue] Active limit (5) reached. Slots: {list(self.active.keys())}")
                        break
                    if not self.pending:
                        log.info("[Queue] No more pending links.")
                        break
                    magnet, idx, user_id, query_key, session = self.pending.pop(0)

                # Send this item outside the lock
                try:
                    refreshed = refresh_fsl_link(magnet)
                    log.info(f"[Queue] [{idx}] Sending to leech group")
                    await self.send_status_log(user_id, f"🚀 Sending task #{idx} to leech group...")

                    cmd = f"/l {refreshed}"
                    if refreshed.lower().endswith((".zip", ".rar")):
                        cmd += " -e"

                    msg = await userbot.send_message(settings.LEECH_GROUP_ID, cmd)

                    async with self.lock:
                        task = ActiveTask(magnet, idx, user_id, query_key, session)
                        self.active[msg.id] = task
                        session["sent_magnets"].add(magnet)

                    await db.mark_leech_sent(user_id, query_key, magnet, idx)
                    session["leech_progress"] = {
                        "sent": len(session["sent_magnets"]),
                        "total": len(session["results"]),
                        "last_idx": idx,
                    }
                    await asyncio.sleep(10)  # flood wait buffer between sends

                except Exception as e:
                    log.error(f"[Queue] Error sending link #{idx}: {e}")
                    await self.send_status_log(user_id, f"⚠️ Error sending link #{idx}: {e}")
                    await asyncio.sleep(2)
        finally:
            async with self.lock:
                self.is_sending = False

    # ── Completion & progress detection ───────────────────────────────────────

    async def handle_message(self, message):
        """
        Called for EVERY message/edit in the leech group.
        - If it's a disk-full error → pause queue, alert admins.
        - If it's a completion → pop the task, schedule next.
        - If it shows 100% upload → mark the oldest task as upload-done
          so the watchdog knows when to auto-skip.
        """
        text = (message.text or message.caption or "")
        if not text:
            return

        # ── Disk full path ────────────────────────────────────────────────────
        if is_disk_full_message(text):
            log.error(f"[Queue] 🛑 DISK FULL detected! Pausing entire queue.")
            async with self.lock:
                self.paused = True
                # Put the failed task back at the front of pending so it retries after disk is cleared
                if self.active:
                    oldest_id = min(self.active.keys())
                    task = self.active.pop(oldest_id)
                    self.pending.insert(0, (task.magnet, task.idx, task.user_id, task.query_key, task.session))
                    log.info(f"[Queue] Task #{task.idx} re-queued at front (will retry after disk cleared).")
            await self.alert_admins(
                "🛑 <b>DISK FULL on leech server!</b>\n"
                "The leech bot reported: <i>No space left on device</i>\n\n"
                "Queue has been <b>PAUSED</b>.\n"
                "Please free up disk space on the server, then:\n"
                "• Use /restart to restart the bot and resume\n"
                "• Or use /cq to clear the queue"
            )
            return

        # ── Completion path ───────────────────────────────────────────────────
        if is_completion_message(text):
            log.info(f"[Queue] ✅ Completion detected: {text[:80]!r}")
            await self._release_task(message, text)
            return

        # ── 100% upload path ──────────────────────────────────────────────────
        if is_upload_complete_message(text):
            async with self.lock:
                if not self.active:
                    return
                # Mark the oldest active task as upload-complete
                oldest_id = min(self.active.keys())
                self.active[oldest_id].mark_upload_done()

    async def _release_task(self, message, text: str):
        """Pop the matching active task and trigger next in queue."""
        is_stopped = "stopped" in text.lower() or "download stopped" in text.lower()

        matched_task: ActiveTask | None = None

        async with self.lock:
            if not self.active:
                log.warning("[Queue] Completion received but no active tasks.")
                return

            target_id = None
            if message.reply_to_message:
                target_id = message.reply_to_message.id
            elif hasattr(message, "reply_to_message_id") and message.reply_to_message_id:
                target_id = message.reply_to_message_id

            if target_id and target_id in self.active:
                matched_task = self.active.pop(target_id)
                log.info(f"[Queue] Task #{matched_task.idx} matched by reply ID.")
            else:
                oldest_id = min(self.active.keys())
                matched_task = self.active.pop(oldest_id)
                log.info(
                    f"[Queue] Task #{matched_task.idx} matched via FIFO "
                    f"(msg_id={oldest_id}). Remaining active: {len(self.active)}"
                )

        # I/O outside the lock
        if is_stopped:
            log.info(f"[Queue] Task #{matched_task.idx} stopped/failed.")
            await self.send_status_log(
                matched_task.user_id,
                f"❌ Task #{matched_task.idx} was stopped or failed by leech bot!"
            )
        else:
            log.info(f"[Queue] Task #{matched_task.idx} completed.")
            await self.send_status_log(
                matched_task.user_id,
                f"✅ Task #{matched_task.idx} completed successfully!"
            )

        asyncio.create_task(self._process_queue())
        log.info(f"[Queue] _process_queue scheduled after task #{matched_task.idx}.")


leech_queue = LeechQueueManager()

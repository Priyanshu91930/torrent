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

# Delay between task completion and sending the next task (seconds)
NEXT_TASK_DELAY = 60


def refresh_fsl_link(url: str) -> str:
    """Strip any old dynamic token suffixes and append current minute token."""
    if not url:
        return url
        
    has_archive = "#archive" in url
    url = url.replace("#archive", "")
    
    if "hub.homelander.buzz" in url or "fsl." in url or "cdn." in url:
        url = re.sub(r'(_1\d+|1\d+)$', '', url)
        minutes = datetime.datetime.now().minute
        if "cdn." in url:
            url += f'_1{minutes}'
        else:
            url += f'1{minutes}'
        log.info(f"[Queue] Refreshed token for FSL link (minute={minutes})")
        
    if has_archive:
        url += "#archive"
        
    return url


def is_completion_message(text: str) -> bool:
    """Return True if text indicates a finished (or failed) leech download."""
    if not text:
        return False
    t = text.lower()
    keywords = [
        "time taken", "download stopped", "stopped!",
        "successfully uploaded", "sent to bot pm", "have been sent",
        "index link:", "completed successfully", "t.me/c/",
    ]
    return any(kw in t for kw in keywords)


def is_progress_update(text: str) -> bool:
    """Return True if the text looks like an active downloading/uploading progress update."""
    t = text.lower()
    if "%" in t and "100%" not in t:
        return True
    if "speed:" in t or "eta:" in t or "downloading" in t:
        if not any(kw in t for kw in ["sent to bot pm", "time taken", "uploaded", "stopped", "t.me/c/"]):
            return True
    return False


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


def is_limit_exceeded_message(text: str) -> bool:
    """Return True if the leech bot says our limit is exceeded."""
    if not text:
        return False
    t = text.lower()
    return "limit exceeded" in t or "tasks limit exceeded" in t


def _get_reply_to_id(message) -> int | None:
    """Extract the reply-to message ID from a Pyrogram message, handling various attribute names."""
    # Try direct attribute first
    if hasattr(message, 'reply_to_message') and message.reply_to_message:
        return message.reply_to_message.id
    # Pyrogram v2 uses reply_to_message_id
    if hasattr(message, 'reply_to_message_id') and message.reply_to_message_id:
        return message.reply_to_message_id
    # Some versions use reply_to_top_message_id for threads
    if hasattr(message, 'reply_to_top_message_id') and message.reply_to_top_message_id:
        return message.reply_to_top_message_id
    return None


class ActiveTask:
    """Holds all state for one active leech slot."""
    __slots__ = (
        "magnet", "idx", "user_id", "query_key", "session",
        "start_time", "upload_done_time", "user_name",
    )

    def __init__(self, magnet, idx, user_id, query_key, session, user_name="Priyanshu"):
        self.magnet = magnet
        self.idx = idx
        self.user_id = user_id
        self.query_key = query_key
        self.session = session
        self.start_time = datetime.datetime.now()
        self.upload_done_time: datetime.datetime | None = None  # set when 100% seen
        self.user_name = user_name

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
        # Case 1: upload hit 100% but no completion in 3 minutes
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
        self.limit_exceeded_cooldown: datetime.datetime | None = None
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
        """Every 60 seconds, check for stuck tasks, and kick stalled queues."""
        while True:
            await asyncio.sleep(60)
            await self._auto_skip_stuck()
            # Also check if queue is stalled (no active tasks but pending items)
            await self._kick_stalled_queue()

    async def _kick_stalled_queue(self):
        """If there are pending items but no active tasks and we're not sending, restart the queue."""
        async with self.lock:
            if self.pending and not self.active and not self.is_sending and not self.paused:
                if not self.limit_exceeded_cooldown or datetime.datetime.now() >= self.limit_exceeded_cooldown:
                    log.info(f"[Queue] Watchdog: queue stalled with {len(self.pending)} pending items. Kicking...")
        # Do this outside the lock
        if self.pending and not self.active and not self.is_sending:
            asyncio.create_task(self._process_queue())

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
            await self._check_job_completion(task.query_key)

        if skipped:
            async def delayed_process():
                log.info(f"[Queue] Waiting {NEXT_TASK_DELAY}s before sending next task (after auto-skip)...")
                await asyncio.sleep(NEXT_TASK_DELAY)
                asyncio.create_task(self._process_queue())
            asyncio.create_task(delayed_process())

    async def _check_job_completion(self, query_key: str):
        """Check if all tasks for a query_key are done (both pending and active), and mark the import job complete."""
        if not query_key:
            return
        async with self.lock:
            for p in self.pending:
                if p[3] == query_key:
                    return
            for t in self.active.values():
                if t.query_key == query_key:
                    return
        await db.mark_import_job_complete(query_key)
        log.info(f"[Queue] Import job {query_key} is fully completed.")

    async def _handle_limit_exceeded(self, message):
        """Handle 'limit exceeded' notification from the leech bot."""
        async with self.lock:
            target_id = _get_reply_to_id(message)

            if target_id and target_id in self.active:
                task = self.active.pop(target_id)
                self.pending.insert(0, (task.magnet, task.idx, task.user_id, task.query_key, task.session))
                self.limit_exceeded_cooldown = datetime.datetime.now() + datetime.timedelta(seconds=180)
                log.info(
                    f"[Queue] Task #{task.idx} re-queued due to limit exceeded. "
                    f"Cooldown set until {self.limit_exceeded_cooldown}."
                )
                await self.send_status_log(
                    task.user_id,
                    f"⚠️ Leech limit exceeded! Task #{task.idx} re-queued. "
                    f"Queue paused for 3 minutes to let tasks finish."
                )
            elif self.active:
                # Can't match by reply — just re-queue the oldest active task
                oldest_id = min(self.active.keys())
                task = self.active.pop(oldest_id)
                self.pending.insert(0, (task.magnet, task.idx, task.user_id, task.query_key, task.session))
                self.limit_exceeded_cooldown = datetime.datetime.now() + datetime.timedelta(seconds=180)
                log.info(
                    f"[Queue] Task #{task.idx} re-queued due to limit exceeded (oldest fallback). "
                    f"Cooldown set until {self.limit_exceeded_cooldown}."
                )
                await self.send_status_log(
                    task.user_id,
                    f"⚠️ Leech limit exceeded! Task #{task.idx} re-queued. "
                    f"Queue paused for 3 minutes to let tasks finish."
                )

            async def retry_after_cooldown():
                await asyncio.sleep(180)
                async with self.lock:
                    if self.limit_exceeded_cooldown and datetime.datetime.now() >= self.limit_exceeded_cooldown:
                        self.limit_exceeded_cooldown = None
                        log.info("[Queue] Limit exceeded cooldown expired.")
                asyncio.create_task(self._process_queue())

            asyncio.create_task(retry_after_cooldown())

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
                    if self.limit_exceeded_cooldown and datetime.datetime.now() < self.limit_exceeded_cooldown:
                        log.info(f"[Queue] Queue in limit exceeded cooldown until {self.limit_exceeded_cooldown}. Stopping send loop.")
                        break
                    active_count = len(self.active)
                    if active_count >= 1:
                        log.info(f"[Queue] Active limit (1) reached. Slots: {list(self.active.keys())}")
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

                    cmd = f"{settings.LEECH_COMMAND} {refreshed}"
                    
                    is_archive = False
                    if "#archive" in cmd:
                        cmd = cmd.replace("#archive", "")
                        is_archive = True

                    # Strip query parameters (e.g. ?token=abc) before checking file extension
                    refreshed_path = refreshed.replace("#archive", "").split("?")[0].lower()
                    if refreshed_path.endswith((".zip", ".rar", ".7z")):
                        is_archive = True
                    
                    is_series = False
                    
                    # Try to find the title for this magnet to check if it's a series
                    title = None
                    if session and "results" in session:
                        for r in session["results"]:
                            r_magnet = r.get("magnet") if isinstance(r, dict) else getattr(r, "magnet", None)
                            if r_magnet == magnet:
                                title = r.get("title") if isinstance(r, dict) else getattr(r, "title", None)
                                break
                                
                    if title:
                        title_lower = title.lower()
                        if "season" in title_lower or "complete" in title_lower or "pack" in title_lower or "episodes" in title_lower:
                            is_series = True
                        elif re.search(r"\bs\d+", title_lower):
                            is_series = True
                        elif re.search(r"ep(?:isodes?|\.)?\s*(\d+)\s*(?:to|-)\s*(\d+)", title_lower):
                            is_series = True
                        elif re.search(r"e(\d+)\s*(?:to|-)\s*e?(\d+)", title_lower):
                            is_series = True

                    if is_archive or is_series:
                        # Append -e only once
                        if not cmd.endswith(" -e"):
                            cmd += " -e"

                    msg = await userbot.send_message(settings.LEECH_GROUP_ID, cmd)

                    async with self.lock:
                        user_name = session.get("user_name", "Priyanshu")
                        task = ActiveTask(magnet, idx, user_id, query_key, session, user_name)
                        self.active[msg.id] = task
                        session["sent_magnets"].add(magnet)

                    await db.mark_leech_sent(user_id, query_key, magnet, idx)
                    session["leech_progress"] = {
                        "sent": len(session["sent_magnets"]),
                        "total": len(session["results"]),
                        "last_idx": idx,
                    }
                    log.info(f"[Queue] [{idx}] Sent successfully. msg_id={msg.id}. Waiting for completion...")
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
        Called for EVERY message/edit in the leech group or PM.
        - If it's a disk-full error → pause queue, alert admins.
        - If it's a limit exceeded error → re-queue task, start cooldown.
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

        # ── Limit exceeded path ────────────────────────────────────────────────
        if is_limit_exceeded_message(text):
            log.warning(f"[Queue] ⚠️ Limit exceeded detected: {text[:80]!r}")
            await self._handle_limit_exceeded(message)
            return

        # ── Completion path ───────────────────────────────────────────────────
        if is_completion_message(text):
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
                log.info("[Queue] Completion received but no active tasks. Kicking queue...")
                asyncio.create_task(self._process_queue())
                return

            # ── Try to match by reply_to_message_id ──────────────────────────
            target_id = _get_reply_to_id(message)

            if target_id and target_id in self.active:
                matched_task = self.active.pop(target_id)
                log.info(f"[Queue] ✅ Task #{matched_task.idx} matched by reply ID ({target_id}).")
            else:
                # ── Fallback: since we only run 1 task at a time, any genuine
                #    completion means our single active task is done ──────────
                #    Verify it's plausibly ours by checking username or PM context
                from pyrogram.enums import ChatType

                is_pm = (message.chat and message.chat.type == ChatType.PRIVATE)
                is_our = False

                if is_pm:
                    is_our = True
                else:
                    # Check if userbot name or username appears in the text
                    ub_first = ""
                    ub_user = ""
                    if userbot and userbot.me:
                        ub_first = (userbot.me.first_name or "").lower()
                        ub_user = (userbot.me.username or "").lower()
                    tl = text.lower()

                    if ub_first and ub_first in tl:
                        is_our = True
                    elif ub_user and ub_user in tl:
                        is_our = True
                    else:
                        # Check any active task's user_name in the text
                        for t in self.active.values():
                            if t.user_name and t.user_name.lower() in tl:
                                is_our = True
                                break

                    # LAST RESORT: If only 1 active task and completion text
                    # has "by:" which is the leech bot's format, just accept it
                    if not is_our and len(self.active) == 1:
                        if "by:" in tl or "sent to bot pm" in tl or "t.me/c/" in tl:
                            is_our = True
                            log.info("[Queue] Accepting completion via single-active-task heuristic.")

                if is_our:
                    oldest_id = min(self.active.keys())
                    matched_task = self.active.pop(oldest_id)
                    log.info(
                        f"[Queue] ✅ Task #{matched_task.idx} matched via fallback "
                        f"(is_pm={is_pm}, target_id={target_id}). "
                        f"Remaining active: {len(self.active)}"
                    )
                else:
                    # Not our task — ignore silently (don't log to avoid noise)
                    return

        # I/O outside the lock
        if is_stopped:
            log.info(f"[Queue] Task #{matched_task.idx} stopped/failed.")
            await self.send_status_log(
                matched_task.user_id,
                f"❌ Task #{matched_task.idx} was stopped or failed by leech bot!"
            )
        else:
            log.info(f"[Queue] Task #{matched_task.idx} completed successfully.")
            await self.send_status_log(
                matched_task.user_id,
                f"✅ Task #{matched_task.idx} completed successfully!"
            )

        # Notify batch search manager
        try:
            from bot.utils.batch_search import batch_search_manager
            await batch_search_manager.on_task_completed(matched_task)
        except Exception as e:
            log.error(f"[Queue] Error notifying batch search manager: {e}")

        await self._check_job_completion(matched_task.query_key)

        # Schedule next task after delay
        async def delayed_process():
            log.info(f"[Queue] Waiting {NEXT_TASK_DELAY}s before sending next task...")
            await asyncio.sleep(NEXT_TASK_DELAY)
            log.info(f"[Queue] Delay complete. Starting next task from queue...")
            asyncio.create_task(self._process_queue())

        asyncio.create_task(delayed_process())
        log.info(f"[Queue] Next task scheduled in {NEXT_TASK_DELAY}s after task #{matched_task.idx}.")


leech_queue = LeechQueueManager()

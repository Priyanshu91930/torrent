"""
progress.py — Async animated progress bar via Telegram message edits.
"""

import asyncio
from typing import Optional

from telegram import Message
from telegram.error import TelegramError

from bot.utils.formatter import format_progress
from bot.utils.logger import log


class ProgressUpdater:
    """
    Sends and continuously updates a Telegram message to show scraping progress.

    Usage:
        updater = ProgressUpdater(message)
        async with updater.track(total_scrapers=5) as tracker:
            tracker.advance(1, found=10, stage="TamilMV")
            ...
    """

    def __init__(self, message: Message, update_interval: float = 1.5):
        self._msg = message
        self._interval = update_interval
        self._current = 0
        self._total = 1
        self._found = 0
        self._stage = ""
        self._task: Optional[asyncio.Task] = None
        self._done = False

    def advance(self, step: int = 1, found: int = 0, stage: str = "") -> None:
        """Call this from the scraper loop to update progress."""
        self._current += step
        self._found = found
        self._stage = stage

    async def _loop(self) -> None:
        while not self._done:
            try:
                text = format_progress(
                    self._current, self._total, self._found, self._stage
                )
                await self._msg.edit_text(text, parse_mode="HTML")
            except TelegramError as e:
                # "Message is not modified" is fine — ignore it silently
                if "not modified" not in str(e).lower():
                    log.debug(f"[Progress] edit_text error: {e}")
            await asyncio.sleep(self._interval)

    async def start(self, total: int) -> None:
        self._total = total
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._done = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # Context-manager support
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.stop()

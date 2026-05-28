"""
cache.py — TTL-based in-memory cache with optional disk persistence.
Supports LRU-style eviction when max size is exceeded.
"""

import asyncio
import hashlib
import os
import pickle
import time
from collections import OrderedDict
from typing import Any, Optional

from bot.utils.logger import log


class TorrentCache:
    """
    Async-safe in-memory cache with TTL expiry and optional disk persistence.

    Usage:
        cache = TorrentCache(ttl=600, max_size=200, cache_dir="cache")
        await cache.set("key", value)
        result = await cache.get("key")  # None if expired or missing
    """

    def __init__(self, ttl: int = 600, max_size: int = 200, cache_dir: str = "cache"):
        self.ttl = ttl
        self.max_size = max_size
        self.cache_dir = cache_dir
        self._store: OrderedDict[str, dict] = OrderedDict()
        self._lock = asyncio.Lock()
        os.makedirs(cache_dir, exist_ok=True)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _make_key(raw: str) -> str:
        """Normalize and hash a search query into a safe cache key."""
        return hashlib.sha256(raw.lower().strip().encode()).hexdigest()[:16]

    def _disk_path(self, key: str) -> str:
        return os.path.join(self.cache_dir, f"{key}.pkl")

    def _is_expired(self, entry: dict) -> bool:
        return time.monotonic() - entry["ts"] > self.ttl

    # ── Core API ──────────────────────────────────────────────────────────────

    async def get(self, raw_key: str) -> Optional[Any]:
        """Return cached value or None if missing/expired."""
        key = self._make_key(raw_key)
        async with self._lock:
            if key in self._store:
                entry = self._store[key]
                if not self._is_expired(entry):
                    # Move to end (most recently used)
                    self._store.move_to_end(key)
                    log.debug(f"[Cache] HIT  {raw_key!r}")
                    return entry["data"]
                else:
                    del self._store[key]
                    log.debug(f"[Cache] EXPIRED  {raw_key!r}")

            # Try disk fallback
            path = self._disk_path(key)
            if os.path.exists(path):
                try:
                    with open(path, "rb") as f:
                        entry = pickle.load(f)
                    if not self._is_expired(entry):
                        self._store[key] = entry
                        log.debug(f"[Cache] DISK HIT  {raw_key!r}")
                        return entry["data"]
                    else:
                        os.remove(path)
                except Exception as e:
                    log.warning(f"[Cache] Disk read error: {e}")

        return None

    async def set(self, raw_key: str, value: Any) -> None:
        """Store a value with the current timestamp."""
        key = self._make_key(raw_key)
        entry = {"data": value, "ts": time.monotonic()}

        async with self._lock:
            self._store[key] = entry
            self._store.move_to_end(key)

            # Evict oldest entries if over max size
            while len(self._store) > self.max_size:
                evicted_key, _ = self._store.popitem(last=False)
                log.debug(f"[Cache] EVICT  {evicted_key}")

            # Persist to disk
            try:
                with open(self._disk_path(key), "wb") as f:
                    pickle.dump(entry, f)
            except Exception as e:
                log.warning(f"[Cache] Disk write error: {e}")

        log.debug(f"[Cache] SET  {raw_key!r}")

    async def delete(self, raw_key: str) -> None:
        key = self._make_key(raw_key)
        async with self._lock:
            self._store.pop(key, None)
            path = self._disk_path(key)
            if os.path.exists(path):
                os.remove(path)

    async def clear(self) -> int:
        """Clear all cached entries. Returns number cleared."""
        async with self._lock:
            count = len(self._store)
            self._store.clear()
            for f in os.listdir(self.cache_dir):
                if f.endswith(".pkl"):
                    os.remove(os.path.join(self.cache_dir, f))
        log.info(f"[Cache] Cleared {count} entries")
        return count

    @property
    def size(self) -> int:
        return len(self._store)

    def stats(self) -> dict:
        return {
            "entries": self.size,
            "max_size": self.max_size,
            "ttl_seconds": self.ttl,
        }


# Singleton
torrent_cache = TorrentCache()

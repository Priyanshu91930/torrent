"""
models.py (database) — SQLite models using aiosqlite.
Tables: users, search_history, favorites, blacklist, leech_sent.
"""

import aiosqlite
import asyncio
import os
import time
from typing import List, Optional

from bot.utils.logger import log


class Database:
    """Async SQLite database manager."""

    def __init__(self, db_path: str = "data/torrentbot.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._create_tables()
        log.info(f"[DB] Connected to {self.db_path}")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def _create_tables(self) -> None:
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                first_name  TEXT,
                joined_at   INTEGER,
                last_seen   INTEGER,
                search_count INTEGER DEFAULT 0,
                is_banned   INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS search_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER,
                query       TEXT,
                results     INTEGER,
                searched_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS favorites (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER,
                title       TEXT,
                magnet      TEXT,
                size        TEXT,
                added_at    INTEGER
            );

            CREATE TABLE IF NOT EXISTS blacklist (
                user_id     INTEGER PRIMARY KEY,
                reason      TEXT,
                banned_at   INTEGER
            );

            CREATE TABLE IF NOT EXISTS leech_sent (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                query_key   TEXT NOT NULL,
                link        TEXT NOT NULL,
                link_idx    INTEGER,
                sent_at     INTEGER,
                UNIQUE(user_id, query_key, link)
            );

            CREATE TABLE IF NOT EXISTS import_jobs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                query_key   TEXT NOT NULL UNIQUE,
                links_json  TEXT NOT NULL,
                total       INTEGER,
                created_at  INTEGER,
                completed   INTEGER DEFAULT 0
            );
        """)
        await self._conn.commit()

    # ── User management ───────────────────────────────────────────────────────

    async def upsert_user(self, user_id: int, username: str = "", first_name: str = "") -> None:
        now = int(time.time())
        await self._conn.execute("""
            INSERT INTO users (user_id, username, first_name, joined_at, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_seen=excluded.last_seen
        """, (user_id, username, first_name, now, now))
        await self._conn.commit()

    async def increment_search_count(self, user_id: int) -> None:
        await self._conn.execute(
            "UPDATE users SET search_count = search_count + 1, last_seen = ? WHERE user_id = ?",
            (int(time.time()), user_id)
        )
        await self._conn.commit()

    async def get_all_users(self) -> List[dict]:
        async with self._conn.execute("SELECT * FROM users") as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_user(self, user_id: int) -> Optional[dict]:
        async with self._conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def get_user_count(self) -> int:
        async with self._conn.execute("SELECT COUNT(*) FROM users") as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    # ── Search history ────────────────────────────────────────────────────────

    async def log_search(self, user_id: int, query: str, results: int) -> None:
        await self._conn.execute(
            "INSERT INTO search_history (user_id, query, results, searched_at) VALUES (?,?,?,?)",
            (user_id, query, results, int(time.time()))
        )
        await self._conn.commit()

    async def get_search_count(self) -> int:
        async with self._conn.execute("SELECT COUNT(*) FROM search_history") as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    async def get_user_history(self, user_id: int, limit: int = 10) -> List[dict]:
        async with self._conn.execute(
            "SELECT query, results, searched_at FROM search_history "
            "WHERE user_id=? ORDER BY searched_at DESC LIMIT ?",
            (user_id, limit)
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_top_queries(self, limit: int = 10) -> List[dict]:
        async with self._conn.execute(
            "SELECT query, COUNT(*) as cnt FROM search_history "
            "GROUP BY LOWER(query) ORDER BY cnt DESC LIMIT ?",
            (limit,)
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── Favorites ─────────────────────────────────────────────────────────────

    async def save_favorite(self, user_id: int, title: str, magnet: str, size: str = "") -> None:
        await self._conn.execute(
            "INSERT INTO favorites (user_id, title, magnet, size, added_at) VALUES (?,?,?,?,?)",
            (user_id, title, magnet, size, int(time.time()))
        )
        await self._conn.commit()

    async def get_favorites(self, user_id: int) -> List[dict]:
        async with self._conn.execute(
            "SELECT id, title, magnet, size, added_at FROM favorites WHERE user_id=? ORDER BY added_at DESC",
            (user_id,)
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def delete_favorite(self, fav_id: int, user_id: int) -> None:
        await self._conn.execute(
            "DELETE FROM favorites WHERE id=? AND user_id=?", (fav_id, user_id)
        )
        await self._conn.commit()

    # ── Blacklist ─────────────────────────────────────────────────────────────

    async def add_blacklist(self, user_id: int, reason: str = "") -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO blacklist (user_id, reason, banned_at) VALUES (?,?,?)",
            (user_id, reason, int(time.time()))
        )
        await self._conn.execute(
            "UPDATE users SET is_banned=1 WHERE user_id=?", (user_id,)
        )
        await self._conn.commit()

    async def remove_blacklist(self, user_id: int) -> None:
        await self._conn.execute("DELETE FROM blacklist WHERE user_id=?", (user_id,))
        await self._conn.execute("UPDATE users SET is_banned=0 WHERE user_id=?", (user_id,))
        await self._conn.commit()

    async def is_banned(self, user_id: int) -> bool:
        async with self._conn.execute(
            "SELECT 1 FROM blacklist WHERE user_id=?", (user_id,)
        ) as cur:
            return await cur.fetchone() is not None

    async def get_blacklist(self) -> List[dict]:
        async with self._conn.execute("SELECT * FROM blacklist") as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── Leech sent tracking ───────────────────────────────────────────────────

    async def mark_leech_sent(self, user_id: int, query_key: str, link: str, link_idx: int) -> None:
        """Persist a sent link so bot knows it was already leeched."""
        await self._conn.execute(
            """
            INSERT OR IGNORE INTO leech_sent (user_id, query_key, link, link_idx, sent_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, query_key, link, link_idx, int(time.time()))
        )
        await self._conn.commit()

    async def get_leech_sent(self, user_id: int, query_key: str) -> set:
        """Return set of already-sent links for a given user+query."""
        async with self._conn.execute(
            "SELECT link FROM leech_sent WHERE user_id=? AND query_key=? ORDER BY link_idx",
            (user_id, query_key)
        ) as cur:
            rows = await cur.fetchall()
        return {row[0] for row in rows}

    async def get_leech_progress(self, user_id: int, query_key: str) -> dict:
        """Return count of sent links and last sent index for a query."""
        async with self._conn.execute(
            "SELECT COUNT(*) as cnt, MAX(link_idx) as last_idx FROM leech_sent WHERE user_id=? AND query_key=?",
            (user_id, query_key)
        ) as cur:
            row = await cur.fetchone()
        return {"sent": row[0] or 0, "last_idx": row[1] or 0}

    async def clear_leech_sent(self, user_id: int, query_key: str) -> None:
        """Clear sent records for a query (to restart from scratch)."""
        await self._conn.execute(
            "DELETE FROM leech_sent WHERE user_id=? AND query_key=?",
            (user_id, query_key)
        )
        await self._conn.commit()

    # ── Import job checkpointing ───────────────────────────────────────────────

    async def save_import_job(self, user_id: int, query_key: str, links: list) -> None:
        """Persist an import job so it can be resumed after restart."""
        import json
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO import_jobs (user_id, query_key, links_json, total, created_at, completed)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (user_id, query_key, json.dumps(links), len(links), int(time.time()))
        )
        await self._conn.commit()

    async def get_pending_import_jobs(self) -> list:
        """Return all unfinished import jobs (for resume on restart)."""
        async with self._conn.execute(
            "SELECT user_id, query_key, links_json, total FROM import_jobs WHERE completed=0 ORDER BY created_at"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_import_job(self, query_key: str) -> Optional[dict]:
        """Return a single import job by query_key."""
        async with self._conn.execute(
            "SELECT user_id, query_key, links_json, total, completed FROM import_jobs WHERE query_key=?",
            (query_key,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def mark_import_job_complete(self, query_key: str) -> None:
        """Mark an import job as finished."""
        await self._conn.execute(
            "UPDATE import_jobs SET completed=1 WHERE query_key=?",
            (query_key,)
        )
        await self._conn.commit()

    async def delete_import_job(self, query_key: str) -> None:
        """Delete an import job record entirely."""
        await self._conn.execute("DELETE FROM import_jobs WHERE query_key=?", (query_key,))
        await self._conn.commit()


# Singleton
db = Database()

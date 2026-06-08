import asyncio
from typing import Dict, List, Set, Optional
from telegram import Bot, Message
from telegram.constants import ParseMode
import datetime

from bot.models import TorrentResult, SearchQuery
from bot.scrapers.hdhub4u import HDHub4uScraper
from bot.utils.logger import log
from bot.db.models import db
from bot.config import settings

import html

class BatchSearchJob:
    def __init__(self, user_id: int, query: SearchQuery, hits: List[dict], query_key: str, status_msg: Message):
        self.user_id = user_id
        self.query = query
        self.hits = hits
        self.query_key = query_key
        self.status_msg = status_msg
        
        self.total_posts = len(hits)
        self.current_hit_idx = 0
        
        # Track links to posts mapping
        self.link_to_post_idx: Dict[str, int] = {}
        self.post_links: Dict[int, Set[str]] = {}
        self.post_titles: Dict[int, str] = {}
        
        # Track progress
        self.completed_links: Set[str] = set()
        self.completed_posts: Set[int] = set()
        self.fetched_posts: Set[int] = set()
        self.has_triggered_next = False
        self.lock = asyncio.Lock()

    def get_progress_text(self) -> str:
        done = len(self.completed_posts)
        pct = int((done / max(self.total_posts, 1)) * 100)
        filled = int(pct / 10)
        bar = "█" * filled + "░" * (10 - filled)
        safe_query = html.escape(self.query.display_query)
        return (
            f"🔍 <b>Batch Leech:</b> <code>{safe_query}</code>\n"
            f"📊 Total Movies/Posts: <b>{self.total_posts}</b>\n"
            f"✅ Movies Completed: <b>{done} / {self.total_posts}</b>\n"
            f"<code>{bar}</code> {pct}%\n"
            f"🌐 Status: <i>Processing batch leech...</i>"
        )

class BatchSearchManager:
    def __init__(self):
        self.jobs: Dict[str, BatchSearchJob] = {}
        self.scraper = HDHub4uScraper(
            timeout=settings.REQUEST_TIMEOUT,
            max_retries=settings.MAX_RETRIES,
            proxy=settings.HTTP_PROXY,
        )

    async def start_job(self, user_id: int, query: SearchQuery, status_msg: Message) -> None:
        # Get hits from Typesense (very fast)
        hits = await self.scraper.fast_search_hits(query)
        if not hits:
            await status_msg.edit_text("😔 No results found for your query.")
            return

        query_key = f"batch_search_{user_id}_{int(datetime.datetime.now().timestamp())}"
        job = BatchSearchJob(user_id, query, hits, query_key, status_msg)
        self.jobs[query_key] = job
        
        # Update progress message
        await status_msg.edit_text(job.get_progress_text(), parse_mode=ParseMode.HTML)
        
        # Fetch the first 10 posts
        await self.fetch_next_batch(job)

    async def fetch_next_batch(self, job: BatchSearchJob) -> None:
        async with job.lock:
            start_idx = job.current_hit_idx
            end_idx = min(start_idx + 10, job.total_posts)
            if start_idx >= end_idx:
                log.info(f"[BatchSearch] No more posts to fetch for {job.query_key}")
                return

            job.current_hit_idx = end_idx
            job.has_triggered_next = False
            
            log.info(f"[BatchSearch] Fetching posts {start_idx} to {end_idx} for {job.query_key}")
            batch_hits = job.hits[start_idx:end_idx]
            
            # Scrape pages for this batch
            results = await self.scraper.process_hits_batch(batch_hits)
            
            if not results:
                log.warning(f"[BatchSearch] No download links found in batch {start_idx}-{end_idx}")
                # Mark these posts as completed since we can't leech them
                for i in range(start_idx, end_idx):
                    job.completed_posts.add(i)
                # Try fetching next batch immediately
                await job.status_msg.edit_text(job.get_progress_text(), parse_mode=ParseMode.HTML)
                asyncio.create_task(self.fetch_next_batch(job))
                return

            # Map the resolved links to their post indices using post_url
            magnets = []
            for res in results:
                # Find which hit matches this result's torrent_url
                matched_idx = -1
                for idx in range(start_idx, end_idx):
                    permalink = job.hits[idx].get("document", {}).get("permalink", "")
                    post_url = self.scraper.base_url + permalink if permalink.startswith("/") else permalink
                    if res.torrent_url == post_url:
                        matched_idx = idx
                        post_title = job.hits[idx].get("document", {}).get("post_title", "")
                        job.post_titles[idx] = post_title
                        break
                
                if matched_idx != -1:
                    if matched_idx not in job.post_links:
                        job.post_links[matched_idx] = set()
                    job.post_links[matched_idx].add(res.magnet)
                    job.link_to_post_idx[res.magnet] = matched_idx
                    magnets.append(res.magnet)

            # Record which posts we actually fetched links for
            for idx in range(start_idx, end_idx):
                job.fetched_posts.add(idx)
                # If a post has no links, it is completed immediately
                if idx not in job.post_links or not job.post_links[idx]:
                    job.completed_posts.add(idx)

            if not magnets:
                # All posts in this batch had no links
                await job.status_msg.edit_text(job.get_progress_text(), parse_mode=ParseMode.HTML)
                asyncio.create_task(self.fetch_next_batch(job))
                return

            # Add to leech queue
            from bot.utils.leech_queue import leech_queue
            user_db = await db.get_user(job.user_id)
            user_name = "Priyanshu"
            if user_db:
                user_name = user_db.get("first_name") or user_db.get("username") or "Priyanshu"

            session = {
                "results": [{"magnet": m, "title": f"Link {i}"} for i, m in enumerate(magnets, 1)],
                "sent_magnets": set(),
                "query": job.query,
                "query_key": job.query_key,
                "user_name": user_name,
            }
            
            log.info(f"[BatchSearch] Queueing {len(magnets)} links for batch {start_idx}-{end_idx}")
            await leech_queue.add_to_queue(magnets, job.user_id, job.query_key, session)

    async def on_task_completed(self, task) -> None:
        query_key = task.query_key
        if query_key not in self.jobs:
            return

        job = self.jobs[query_key]
        magnet = task.magnet
        
        async with job.lock:
            job.completed_links.add(magnet)
            
            post_idx = job.link_to_post_idx.get(magnet)
            if post_idx is not None:
                # Check if all links for this post are completed
                links_for_post = job.post_links.get(post_idx, set())
                if links_for_post and links_for_post.issubset(job.completed_links):
                    job.completed_posts.add(post_idx)
                    log.info(f"[BatchSearch] Post {post_idx} ('{job.post_titles.get(post_idx)}') completed!")

            # Update progress message
            try:
                await job.status_msg.edit_text(job.get_progress_text(), parse_mode=ParseMode.HTML)
            except Exception as e:
                log.error(f"[BatchSearch] Error editing status: {e}")

            # Check if we should trigger the next batch of 10 posts
            # "when it reaches till 9 post it fetch again 10 post"
            # This means: when the 9th post is completed (or processed) in the current batch
            # Or if we have completed at least 9 posts of the currently fetched ones
            fetched_sorted = sorted(list(job.fetched_posts))
            if len(fetched_sorted) >= 9:
                # Get the index of the 9th post in the current set of fetched posts
                # For example, if we fetched posts 0..9, the 9th post is index 8 (0-based)
                # Wait, "when it reaches till 9 post" -> 9th post completed
                ninth_post_idx = fetched_sorted[-2] if len(fetched_sorted) > 9 else fetched_sorted[8]
                if ninth_post_idx in job.completed_posts and not job.has_triggered_next:
                    job.has_triggered_next = True
                    log.info(f"[BatchSearch] 9th post reached. Fetching next batch of 10 posts...")
                    asyncio.create_task(self.fetch_next_batch(job))

            # If all posts in the entire search query are completed
            if len(job.completed_posts) >= job.total_posts:
                try:
                    await job.status_msg.reply_text(
                        f"🎉 <b>Batch Leech Completed!</b>\n"
                        f"✅ Leeched all matching posts for query: <code>{job.query.display_query}</code>",
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    log.error(f"[BatchSearch] Error sending finished notification: {e}")
                self.jobs.pop(query_key, None)

batch_search_manager = BatchSearchManager()

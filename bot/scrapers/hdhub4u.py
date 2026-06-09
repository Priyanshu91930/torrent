import asyncio
import datetime
import re
import base64
from typing import List, Optional
import aiohttp
from bs4 import BeautifulSoup
from curl_cffi import requests

from bot.models import TorrentResult, SearchQuery
from bot.scrapers.base import BaseScraper
from bot.utils.logger import log
from bot.config import settings

def rot13(s: str) -> str:
    out = []
    for c in s:
        if 'a' <= c <= 'z':
            out.append(chr((ord(c) - 97 + 13) % 26 + 97))
        elif 'A' <= c <= 'Z':
            out.append(chr((ord(c) - 65 + 13) % 26 + 65))
        else:
            out.append(c)
    return "".join(out)

def decrypt_gadgets_payload(payload: str) -> Optional[str]:
    """Decrypt the gadgetsweb.xyz redirection payload."""
    try:
        # Step 1: Base64 decode
        dec1 = base64.b64decode(payload).decode('utf-8', errors='ignore')
        # Step 2: Base64 decode again
        dec2 = base64.b64decode(dec1).decode('utf-8', errors='ignore')
        # Step 3: Rot13 decode
        rot2 = rot13(dec2)
        # Step 4: Base64 decode with proper padding
        padded = rot2 + "=" * ((4 - len(rot2) % 4) % 4)
        dec3 = base64.b64decode(padded).decode('utf-8', errors='ignore')
        
        # Parse JSON keys manually or using json
        import json
        data = json.loads(dec3)
        encoded_url = data.get('o')
        if encoded_url:
            return base64.b64decode(encoded_url).decode('utf-8', errors='ignore')
    except Exception as e:
        log.error(f"[HDHub4u] Decryption failed: {e}")
    return None

def get_episode_count(text: str) -> int:
    """Parse episode range or count from text (e.g. Ep 1 to 8 -> 8)."""
    if not text:
        return 1
    text_lower = text.lower()
    
    # Look for patterns like Ep 1 to 8, Ep 1-8, Episode 1-10, Ep 01-08
    m = re.search(r"ep(?:isodes?|\.)?\s*(\d+)\s*(?:to|-)\s*(\d+)", text_lower)
    if m:
        start = int(m.group(1))
        end = int(m.group(2))
        return max(1, end - start + 1)
        
    # Pattern 2: e01-e08, e01-08
    m2 = re.search(r"e(\d+)\s*(?:to|-)\s*e?(\d+)", text_lower)
    if m2:
        start = int(m2.group(1))
        end = int(m2.group(2))
        return max(1, end - start + 1)

    # Fallback: if it's clearly a series/season pack but we don't know the exact count, default to 8 episodes
    if "season" in text_lower or "complete" in text_lower or "pack" in text_lower or "episodes" in text_lower or re.search(r"\bs\d+", text_lower):
        return 8

    return 1

class HDHub4uScraper(BaseScraper):
    """
    HDHub4uScraper — Scrapes direct FSL/FSLv2 download links for 1080p movies under 4GB.
    """

    name = "HDHub4u"

    @property
    def base_url(self) -> str:
        return settings.HDHUB4U_URL

    typesense_url = "https://search.pingora.fyi/collections/post/documents/search"

    def _http_get(self, url: str, params: Optional[dict] = None, referer: Optional[str] = None, cookies: Optional[dict] = None) -> Optional[str]:
        """Perform a synchronous HTTP GET request using curl_cffi impersonate."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        if referer:
            headers["Referer"] = referer
        try:
            r = requests.get(
                url,
                params=params,
                headers=headers,
                cookies=cookies,
                impersonate="chrome110",
                timeout=30,
                allow_redirects=True,
                proxy=self.proxy
            )
            if r.status_code == 200:
                return r.text
            else:
                log.warning(f"[{self.name}] HTTP {r.status_code} for {url}")
        except Exception as e:
            log.error(f"[{self.name}] Fetch error for {url}: {e}")
        return None

    async def fast_search_hits(self, query: SearchQuery) -> List[dict]:
        log.info(f"[{self.name}] Fast searching hits: {query.query}")
        
        # 1. Fetch first page of search results from Typesense
        LIMIT_PER_PAGE = 250
        MAX_PAGES = 40  # Fetch up to 40 pages (10,000 hits) to get all results

        def fetch_search(page: int):
            params = {
                "q": query.query,
                "query_by": "post_title,category,stars,director,imdb_id",
                "sort_by": "sort_by_date:desc",
                "limit": str(LIMIT_PER_PAGE),
                "page": str(page)
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
                "Accept": "*/*",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/"
            }
            try:
                r = requests.get(
                    self.typesense_url,
                    params=params,
                    headers=headers,
                    impersonate="chrome110",
                    timeout=20,
                    proxy=self.proxy
                )
                if r.status_code == 200:
                    return r.json()
                else:
                    log.warning(f"[{self.name}] Typesense search returned status code {r.status_code}: {r.text}")
            except Exception as e:
                log.error(f"[{self.name}] Typesense fetch failed: {e}")
            return None

        first_page = await asyncio.to_thread(fetch_search, 1)
        if not first_page or not first_page.get("hits"):
            log.info(f"[{self.name}] No results found for '{query.query}'")
            return []

        found = first_page.get("found", 0)
        # Calculate pages needed to get all results, capped at MAX_PAGES
        total_pages = min(MAX_PAGES, (found + LIMIT_PER_PAGE - 1) // LIMIT_PER_PAGE)
        log.info(f"[{self.name}] Total hits on server: {found}, fetching {total_pages} pages")
        
        # Fetch remaining pages concurrently
        pages_to_fetch = list(range(2, total_pages + 1))
        all_hits = list(first_page.get("hits", []))

        if pages_to_fetch:
            tasks = [asyncio.to_thread(fetch_search, p) for p in pages_to_fetch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if r and isinstance(r, dict) and r.get("hits"):
                    all_hits.extend(r.get("hits"))

        log.info(f"[{self.name}] Found {len(all_hits)} hits from Typesense")
        return all_hits

    async def process_hits_batch(self, hits: List[dict]) -> List[TorrentResult]:
        sem = asyncio.Semaphore(10)  # limit concurrency to avoid ban
        
        async def process_hit(hit) -> List[TorrentResult]:
            doc = hit.get("document", {})
            title = doc.get("post_title", "")
            permalink = doc.get("permalink", "")
            thumbnail = doc.get("post_thumbnail")
            
            if not permalink:
                return []
                
            post_url = self.base_url + permalink if permalink.startswith("/") else permalink
            
            async with sem:
                html = await asyncio.to_thread(self._http_get, post_url)
                
            if not html:
                return []
                
            soup = BeautifulSoup(html, "lxml")
            movie_results: List[TorrentResult] = []

            # Accepted quality keywords for video files
            QUALITY_KEYWORDS = ["480p", "720p", "1080p", "2160p", "4k"]
            # Archive file extensions
            ARCHIVE_EXTENSIONS = (".zip", ".rar", ".7z")
            # Per-episode size cap for video files (4 GB per episode)
            VIDEO_SIZE_CAP_GB_PER_EP = 4.0
            
            # Detect episode count from post title for dynamic size cap
            episode_count = get_episode_count(title)
            
            # Determine size limit for this post/link
            # If the post has multiple episodes, we use episode_count * 4.0 GB as the limit
            if episode_count > 1:
                max_allowed_size_gb = VIDEO_SIZE_CAP_GB_PER_EP * episode_count
            else:
                max_allowed_size_gb = VIDEO_SIZE_CAP_GB_PER_EP

            # Find all download links
            for a in soup.find_all("a"):
                href = a.get("href")
                text = a.get_text(strip=True)
                if not href or not text:
                    continue

                text_lower = text.lower()
                # Strip query params before checking extension (e.g. file.zip?token=abc)
                href_path = href.split("?")[0].lower()

                # Check if this is an archive link (zip/rar/7z)
                is_archive = href_path.endswith(ARCHIVE_EXTENSIONS) or any(
                    ext in href_path for ext in ARCHIVE_EXTENSIONS
                )

                # Check if link contains a quality keyword
                matched_quality = next((q for q in QUALITY_KEYWORDS if q in text_lower), None)

                # Skip links that are neither archive nor a recognized quality
                if not is_archive and not matched_quality:
                    continue

                # Parse size e.g. [2GB], [1.7GB], [930MB]
                m = re.search(r"([\d.]+)\s*(gb|mb)", text_lower)
                size_str = "Unknown"
                if m:
                    size_val = float(m.group(1))
                    unit = m.group(2)
                    size_gb = size_val if unit == "gb" else size_val / 1024.0
                    size_str = f"{size_val:.2f} GB" if unit == "gb" else f"{size_val} MB"

                    if size_gb >= max_allowed_size_gb:
                        log.info(f"[HDHub4u] Skipping link {text} because size {size_str} >= cap {max_allowed_size_gb:.1f}GB ({episode_count} eps)")
                        continue
                elif not is_archive:
                    # If we can't parse size and it's not an archive, skip it
                    continue

                # Resolve active FSL links
                fsl_link = await self._resolve_fsl_link(href, post_url)
                if fsl_link:
                    res_title = f"{title} - {text}"
                    movie_results.append(TorrentResult(
                        title=res_title,
                        magnet=fsl_link,
                        torrent_url=post_url,
                        size=size_str,
                        source=self.name,
                        category="movie",
                        thumbnail=thumbnail
                    ))

            return movie_results

        tasks = [process_hit(hit) for hit in hits]
        movies_list = await asyncio.gather(*tasks, return_exceptions=True)
        
        flat_results: List[TorrentResult] = []
        for movies in movies_list:
            if isinstance(movies, list):
                flat_results.extend(movies)
                
        log.info(f"[{self.name}] Scraped {len(flat_results)} direct links matching criteria")
        return flat_results

    async def search(
        self, query: SearchQuery, session: aiohttp.ClientSession
    ) -> List[TorrentResult]:
        hits = await self.fast_search_hits(query)
        if not hits:
            return []
        return await self.process_hits_batch(hits)

    async def run_quality_report(self, query: SearchQuery, max_pages_to_check: int = 2) -> dict:
        """
        Runs a report to find posts that do not contain the QUALITY_KEYWORDS.
        """
        log.info(f"[{self.name}] Running quality report for: {query.query}")
        
        # 1. Fetch search results from Typesense
        LIMIT_PER_PAGE = 250
        
        def fetch_search(page: int):
            params = {
                "q": query.query,
                "query_by": "post_title,category,stars,director,imdb_id",
                "sort_by": "sort_by_date:desc",
                "limit": str(LIMIT_PER_PAGE),
                "page": str(page)
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
                "Accept": "*/*",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/"
            }
            try:
                r = requests.get(
                    self.typesense_url,
                    params=params,
                    headers=headers,
                    impersonate="chrome110",
                    timeout=20,
                    proxy=self.proxy
                )
                if r.status_code == 200:
                    return r.json()
            except Exception as e:
                log.error(f"[{self.name}] Typesense fetch failed: {e}")
            return None

        first_page = await asyncio.to_thread(fetch_search, 1)
        if not first_page or not first_page.get("hits"):
            return {
                "total_website_items": 0,
                "processed_items_count": 0,
                "skipped_posts": []
            }

        found = first_page.get("found", 0)
        total_pages = min(max_pages_to_check, (found + LIMIT_PER_PAGE - 1) // LIMIT_PER_PAGE)
        
        pages_to_fetch = list(range(2, total_pages + 1))
        all_hits = list(first_page.get("hits", []))

        if pages_to_fetch:
            tasks = [asyncio.to_thread(fetch_search, p) for p in pages_to_fetch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if r and isinstance(r, dict) and r.get("hits"):
                    all_hits.extend(r.get("hits"))

        sem = asyncio.Semaphore(10)
        skipped_posts = []

        async def check_hit(hit):
            doc = hit.get("document", {})
            title = doc.get("post_title", "")
            permalink = doc.get("permalink", "")
            
            if not permalink:
                return
                
            post_url = self.base_url + permalink if permalink.startswith("/") else permalink
            
            async with sem:
                html = await asyncio.to_thread(self._http_get, post_url)
                
            if not html:
                return

            soup = BeautifulSoup(html, "lxml")
            QUALITY_KEYWORDS = ["480p", "720p", "1080p", "2160p", "4k"]
            
            has_download_links = False
            has_matching_quality = False
            
            for a in soup.find_all("a"):
                href = a.get("href")
                text = a.get_text(strip=True)
                if not href or not text:
                    continue
                
                href_lower = href.lower()
                text_lower = text.lower()
                
                is_download = (
                    "hubdrive" in href_lower or 
                    "hubcdn" in href_lower or 
                    "hubcloud" in href_lower or 
                    "pixeldrain" in href_lower or 
                    "gadgetsweb" in href_lower or
                    "cdn." in href_lower or
                    "hub." in href_lower or
                    bool(re.search(r"([\d.]+)\s*(gb|mb)", text_lower))
                )
                
                if is_download:
                    has_download_links = True
                    if any(q in text_lower for q in QUALITY_KEYWORDS):
                        has_matching_quality = True
                        break
            
            if has_download_links and not has_matching_quality:
                skipped_posts.append(title)
            elif not has_download_links:
                skipped_posts.append(f"{title} (No links found)")

        tasks = [check_hit(hit) for hit in all_hits]
        await asyncio.gather(*tasks, return_exceptions=True)

        return {
            "total_website_items": found,
            "processed_items_count": len(all_hits),
            "skipped_posts": skipped_posts
        }

    async def _resolve_fsl_link(self, initial_url: str, post_url: str) -> Optional[str]:
        """Trace landing/redirect pages to fetch active FSL link."""
        try:
            url = initial_url
            
            # Hop 1: Handle Shorteners / Intermediates (e.g. gadgetsweb.xyz)
            if "gadgetsweb.xyz" in url:
                html = await asyncio.to_thread(self._http_get, url, referer=post_url)
                if not html:
                    return None
                    
                # Decrypt gadgetsweb redirection payload
                # Look for pattern: s('o', 'payload', 180*1000)
                m = re.search(r"s\(\s*'o'\s*,\s*'(.*?)'", html)
                if not m:
                    return None
                decrypted_url = decrypt_gadgets_payload(m.group(1))
                if not decrypted_url:
                    return None
                
                # Fetch hblinks.org page
                html = await asyncio.to_thread(self._http_get, decrypted_url, referer=url)
                if not html:
                    return None
                
                soup = BeautifulSoup(html, "lxml")
                # Look for hubcloud.foo/drive/ link
                hubcloud_anchor = soup.find("a", href=re.compile(r"hubcloud\.(foo|in|club|fans)/drive/"))
                if not hubcloud_anchor:
                    return None
                url = hubcloud_anchor["href"]

            # Hop 2: Handle HubDrive / HubCDN pages
            elif "hubdrive." in url or "hubcdn." in url:
                html = await asyncio.to_thread(self._http_get, url, referer=post_url)
                if not html:
                    return None
                soup = BeautifulSoup(html, "lxml")
                # Find direct HubCloud server link
                hubcloud_anchor = soup.find("a", href=re.compile(r"hubcloud\.(foo|in|club|fans)/drive/"))
                if not hubcloud_anchor:
                    return None
                url = hubcloud_anchor["href"]

            # Hop 3: Fetch HubCloud direct link generation page
            if "hubcloud." in url and "/drive/" in url:
                html = await asyncio.to_thread(self._http_get, url, referer=initial_url)
                if not html:
                    return None
                soup = BeautifulSoup(html, "lxml")
                
                # Find anchor with text containing Generate Direct Download Link
                generate_anchor = None
                for a in soup.find_all("a"):
                    if "Generate Direct Download Link" in a.get_text():
                        generate_anchor = a
                        break
                        
                if not generate_anchor or not generate_anchor.get("href"):
                    return None
                
                gamerxyt_url = generate_anchor["href"]
                
                # Hop 4: Fetch gamerxyt.com landing page containing the FSL Server links
                html = await asyncio.to_thread(self._http_get, gamerxyt_url, referer=url)
                if not html:
                    return None
                
                soup = BeautifulSoup(html, "lxml")

                # Priority 1: Dynamic FSL / FSLv2 links (e.g. cdn.fsl-buckets.work)
                fslv2_anchor = soup.find("a", id="s3") or soup.find("a", href=re.compile(r"cdn\."))
                fsl_anchor = soup.find("a", id="fsl") or soup.find("a", href=re.compile(r"hub\."))
                
                minutes = datetime.datetime.now().minute
                
                if fslv2_anchor and fslv2_anchor.get("href"):
                    fsl_link = fslv2_anchor["href"] + f'_1{minutes}'
                    log.info(f"[HDHub4u] Found Dynamic FSLv2 link: {fsl_link}")
                    return fsl_link
                elif fsl_anchor and fsl_anchor.get("href"):
                    fsl_link = fsl_anchor["href"] + f'1{minutes}'
                    log.info(f"[HDHub4u] Found Dynamic FSL link: {fsl_link}")
                    return fsl_link

                # Priority 2: Pixeldrain Link (Stable direct download fallback)
                pxl_match = re.search(r'var pxl\s*=\s*["\'](https?://[^"\']+)["\']', html)
                if pxl_match:
                    pxl_url = pxl_match.group(1).replace("pixeldrain.dev", "pixeldrain.com")
                    log.info(f"[HDHub4u] Found Pixeldrain link: {pxl_url}")
                    return pxl_url

                pxl_anchor = soup.find("a", id=re.compile(r"pxl")) or soup.find("a", href=re.compile(r"pixeldrain\."))
                if pxl_anchor and pxl_anchor.get("href"):
                    pxl_url = pxl_anchor["href"].replace("pixeldrain.dev", "pixeldrain.com")
                    log.info(f"[HDHub4u] Found Pixeldrain link from anchor: {pxl_url}")
                    return pxl_url

                # Priority 3: Direct Server link (Hubcloud/Pixel 10Gbps fallback)
                pixel_anchor = soup.find("a", href=re.compile(r"pixel\.hubcloud\."))
                if pixel_anchor and pixel_anchor.get("href"):
                    log.info(f"[HDHub4u] Found 10Gbps Hubcloud link: {pixel_anchor['href']}")
                    return pixel_anchor["href"]

        except Exception as e:
            log.error(f"[{self.name}] Error resolving FSL link for {initial_url}: {e}")
            
        return None

"""
scraper.py - Fetches multi-page stories with automatic pagination detection.
Uses httpx (async) + BeautifulSoup4 + tenacity for retries.
"""
from __future__ import annotations

import asyncio
import re
from typing import AsyncGenerator, Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import (
    MAX_PAGES,
    REQUEST_RETRIES,
    REQUEST_RETRY_WAIT,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from app.logger import get_logger

log = get_logger(__name__)

# ── CSS selectors / text that identify "Next page" links ─────────────────────
_NEXT_PATTERNS = [
    # text-based
    re.compile(r"^\s*(next|next\s*page|»|›|→|>>)\s*$", re.I),
]
_NEXT_ATTRS = [
    {"rel": re.compile(r"next", re.I)},
    {"aria-label": re.compile(r"next", re.I)},
    {"class": re.compile(r"next", re.I)},
    {"id": re.compile(r"next", re.I)},
]


# ─────────────────────────────────────────────────────────────────────────────
# Low-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        follow_redirects=True,
        timeout=REQUEST_TIMEOUT,
        http2=True,
    )


async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    """Fetch a URL with exponential back-off retry."""
    async for attempt in AsyncRetrying(
        retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
        stop=stop_after_attempt(REQUEST_RETRIES),
        wait=wait_exponential(multiplier=REQUEST_RETRY_WAIT, min=2, max=30),
        reraise=True,
    ):
        with attempt:
            resp = await client.get(url)
            resp.raise_for_status()
            log.debug("Fetched %s [%d]", url, resp.status_code)
            return resp.text
    return ""   # unreachable, satisfies type-checker


# ─────────────────────────────────────────────────────────────────────────────
# Next-page detection
# ─────────────────────────────────────────────────────────────────────────────

def _find_next_url(soup: BeautifulSoup, current_url: str) -> Optional[str]:
    """
    Try multiple heuristics to discover the next-page link.
    Returns an absolute URL or None.
    """
    base = "{uri.scheme}://{uri.netloc}".format(uri=urlparse(current_url))

    # 1. <link rel="next">
    link_tag = soup.find("link", rel="next")
    if link_tag and link_tag.get("href"):
        return urljoin(base, link_tag["href"])

    # 2. <a> tags matching next-page text/class/id/aria patterns
    for a in soup.find_all("a", href=True):
        href: str = a["href"].strip()
        if not href or href.startswith("javascript"):
            continue
        text = a.get_text(strip=True)
        # text match
        if any(p.match(text) for p in _NEXT_PATTERNS):
            return urljoin(current_url, href)
        # attribute match
        for attr_filter in _NEXT_ATTRS:
            if a.find_parent(attrs=attr_filter) or _tag_matches_attrs(a, attr_filter):
                return urljoin(current_url, href)

    # 3. Sequential URL pattern – try incrementing a trailing page number
    incremented = _increment_url_page(current_url)
    if incremented:
        return incremented

    return None


def _tag_matches_attrs(tag, attr_filter: dict) -> bool:
    for attr, pattern in attr_filter.items():
        val = tag.get(attr, "")
        if isinstance(val, list):
            val = " ".join(val)
        if pattern.search(val):
            return True
    return False


def _increment_url_page(url: str) -> Optional[str]:
    """Try to find a trailing page/chapter number and increment it."""
    # e.g. /chapter-5, /page/5, ?page=5, /5
    patterns = [
        (r"(page[=/])(\d+)", lambda m: m.group(1) + str(int(m.group(2)) + 1)),
        (r"(chapter[=/])(\d+)", lambda m: m.group(1) + str(int(m.group(2)) + 1)),
        (r"([?&]p=)(\d+)", lambda m: m.group(1) + str(int(m.group(2)) + 1)),
        (r"(/\d+)(/?)$", lambda m: "/" + str(int(m.group(1).strip("/")) + 1) + m.group(2)),
    ]
    for pat, repl in patterns:
        new_url, n = re.subn(pat, repl, url)
        if n:
            return new_url
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

async def iter_pages(
    start_url: str,
    job_logger=None,
) -> AsyncGenerator[tuple[int, str, BeautifulSoup], None]:
    """
    Async generator that yields (page_num, page_url, soup) for every page
    in the story, following pagination automatically.

    Usage:
        async for page_num, url, soup in iter_pages(start_url):
            ...
    """
    logger = job_logger or log
    visited: set[str] = set()
    current_url: Optional[str] = start_url
    page_num = 0

    async with _make_client() as client:
        while current_url and page_num < MAX_PAGES:
            if current_url in visited:
                logger.warning("Detected loop at %s – stopping.", current_url)
                break
            visited.add(current_url)
            page_num += 1

            try:
                html = await _fetch(client, current_url)
            except Exception as exc:
                logger.error("Failed to fetch page %d (%s): %s", page_num, current_url, exc)
                break

            soup = BeautifulSoup(html, "html.parser")
            logger.info("Page %d scraped: %s", page_num, current_url)
            yield page_num, current_url, soup

            next_url = _find_next_url(soup, current_url)
            if next_url and next_url not in visited:
                current_url = next_url
                await asyncio.sleep(0.5)   # polite crawl delay
            else:
                logger.info("No further pagination found after page %d.", page_num)
                break

"""
Example scraper backend using plain `requests` — a TEMPLATE for writing your own.

Selected with SCRAPER_PROVIDER=requests. It implements the two primitives the
scraper seam requires:

    scrape_page(url)               -> (html, error)
    serp_search(query, num_results) -> (list[dict], error)

This is intentionally minimal and unauthenticated, so it works as a starting
point with zero API keys. Heads-up: a bare `requests` client has no proxy
rotation or CAPTCHA handling, so Google will throttle/block the SERP calls
quickly and many sites will return 403. For real workloads, copy this file and
wire the two functions to your scraping provider of choice (ScraperAPI, Zyte,
Oxylabs, a Playwright service, etc.), then register it in scraper_service.py.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import quote_plus

import requests

from config import settings

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def scrape_page(url: str) -> tuple[Optional[str], Optional[str]]:
    """Fetch raw HTML of *url* with a plain GET. Returns (html, error)."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=settings.REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.text, None
    except requests.HTTPError as exc:
        msg = f"HTTP {exc.response.status_code} while scraping {url}"
        logger.warning(msg)
        return None, msg
    except requests.RequestException as exc:
        msg = f"Request error scraping {url}: {exc}"
        logger.warning(msg)
        return None, msg


def serp_search(query: str, num_results: int = 10) -> tuple[list[dict], Optional[str]]:
    """
    Run a Google search via a plain GET and parse organic result URLs.
    Returns (results, error); each result has position, title, url, snippet.

    Note: without proxies this gets blocked fast — replace with a real SERP
    provider for production use.
    """
    try:
        google_url = (
            f"https://www.google.com/search"
            f"?q={quote_plus(query)}&num={num_results}&hl=en&gl=us"
        )
        resp = requests.get(google_url, headers=_HEADERS, timeout=settings.REQUEST_TIMEOUT)
        resp.raise_for_status()
        return _parse_google_html(resp.text), None
    except requests.RequestException as exc:
        msg = f"Request error for SERP query '{query}': {exc}"
        logger.warning(msg)
        return [], msg


def _parse_google_html(html: str) -> list[dict]:
    """Extract unique organic https result URLs from a Google results page."""
    results: list[dict] = []
    seen: set[str] = set()
    for m in re.finditer(r'href="(https?://(?!(?:www\.)?google\.)[^"#?]+)"', html):
        url = m.group(1)
        if any(skip in url for skip in ("google.", "gstatic.", "googleapis.", "youtube.com/watch")):
            continue
        if url in seen:
            continue
        seen.add(url)
        results.append({"position": len(results) + 1, "title": "", "url": url, "snippet": ""})
        if len(results) >= 10:
            break
    return results

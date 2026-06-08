"""
Bright Data service.

Provides two capabilities:
  1. scrape_page(url)    – fetch raw HTML of any URL via the Web Unlocker REST API
                          POST https://api.brightdata.com/request
                          Auth: Bearer API key  |  Body: {"zone": "web_unlocker1", ...}

  2. serp_search(query)  – run a Google search via the SERP API REST endpoint
                          POST https://api.brightdata.com/serp/req
                          Auth: Bearer API key  |  Body: {"zone": "serp_api_...", ...}

Authentication: a single Bright Data API key (Bearer token).
Obtain it at: https://brightdata.com/cp/setting/users
"""

from __future__ import annotations

import json
import logging
from typing import Optional
from urllib.parse import quote_plus

import requests

from config import settings

logger = logging.getLogger(__name__)


# ── Auth helper ────────────────────────────────────────────────────────────────

def _auth_headers() -> dict[str, str]:
    if not settings.BRIGHTDATA_API_KEY:
        raise ValueError(
            "BRIGHTDATA_API_KEY is not configured. "
            "Get it from https://brightdata.com/cp/setting/users"
        )
    return {
        "Authorization": f"Bearer {settings.BRIGHTDATA_API_KEY}",
        "Content-Type": "application/json",
    }


# ── Web Unlocker (REST API) ────────────────────────────────────────────────────

def scrape_page(url: str) -> tuple[Optional[str], Optional[str]]:
    """
    Fetch the full HTML of *url* via Bright Data Web Unlocker REST API.

    Endpoint: POST https://api.brightdata.com/request
    Body: {"zone": "<zone_name>", "url": "<target_url>", "format": "raw"}

    Returns (html_content, error_message).
    On success error_message is None; on failure html_content is None.
    """
    try:
        payload = {
            "zone": settings.BRIGHTDATA_WEB_UNLOCKER_ZONE,
            "url": url,
            "format": "raw",
        }
        resp = requests.post(
            settings.BRIGHTDATA_UNLOCKER_ENDPOINT,
            headers=_auth_headers(),
            json=payload,
            timeout=settings.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()

        # The REST API returns JSON with a "body" key for format="raw"
        try:
            data = resp.json()
            html = data.get("body", data.get("content", resp.text))
        except (json.JSONDecodeError, ValueError):
            html = resp.text

        return html, None

    except requests.HTTPError as exc:
        msg = f"HTTP {exc.response.status_code} while scraping {url}: {exc.response.text[:200]}"
        logger.warning(msg)
        return None, msg
    except requests.RequestException as exc:
        msg = f"Request error scraping {url}: {exc}"
        logger.warning(msg)
        return None, msg
    except ValueError as exc:
        return None, str(exc)


# ── SERP API (REST API) ────────────────────────────────────────────────────────

def serp_search(query: str, num_results: int = 10) -> tuple[list[dict], Optional[str]]:
    """
    Run a Google search by scraping Google via the Web Unlocker REST API.

    The dedicated SERP REST endpoint (/serp/req) returns only a response_id
    (async mode) with no working poll endpoint, so instead we fetch the Google
    search results page through Web Unlocker and parse result URLs from the HTML.

    Endpoint: POST https://api.brightdata.com/request  (Web Unlocker zone)

    Returns (results_list, error_message).
    Each result dict has keys: position, title, url, snippet.
    """
    from urllib.parse import quote_plus as _qp
    try:
        google_url = (
            f"https://www.google.com/search"
            f"?q={_qp(query)}&num={num_results}&hl=en&gl=us"
        )
        payload = {
            "zone": settings.BRIGHTDATA_WEB_UNLOCKER_ZONE,
            "url": google_url,
            "format": "raw",
        }
        resp = requests.post(
            settings.BRIGHTDATA_UNLOCKER_ENDPOINT,
            headers=_auth_headers(),
            json=payload,
            timeout=settings.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return _parse_google_html(resp.text, query), None

    except requests.HTTPError as exc:
        msg = f"HTTP {exc.response.status_code} for SERP query '{query}': {exc.response.text[:200]}"
        logger.warning(msg)
        return [], msg
    except requests.RequestException as exc:
        msg = f"Request error for SERP query '{query}': {exc}"
        logger.warning(msg)
        return [], msg
    except ValueError as exc:
        return [], str(exc)


def _parse_google_html(html: str, query: str) -> list[dict]:
    """
    Extract organic result URLs from a Google search result HTML page.

    Google embeds result URLs as plain https:// hrefs in <a> tags inside
    result divs. We extract unique https:// links that are not Google-internal.
    """
    import re
    results = []
    seen: set[str] = set()

    # Match direct https:// URLs in href attributes (Google search result links)
    for m in re.finditer(r'href="(https?://(?!(?:www\.)?google\.)[^"#?]+)"', html):
        url = m.group(1)
        # Skip Google's own domains, AMP, and non-result URLs
        if any(skip in url for skip in ("google.", "gstatic.", "googleapis.", "youtube.com/watch")):
            continue
        if url in seen:
            continue
        seen.add(url)
        results.append({
            "position": len(results) + 1,
            "title": "",
            "url": url,
            "snippet": "",
        })
        if len(results) >= 10:
            break

    if not results:
        logger.debug("No SERP results parsed from Google HTML for query '%s'", query)
    return results


# ── Convenience wrappers for site: searches ────────────────────────────────────

# Terms used for individual site: SERP checks. Simple single-word queries work
# more reliably than complex OR/parenthesis expressions with Bright Data's SERP API.
_SERP_PORN_GAMBLING_TERMS = [
    "casino", "gambling", "porn", "sex", "xxx",
    "betting", "slots", "escort", "blackjack", "poker",
]


def site_search_porn_gambling(domain: str) -> tuple[list[dict], Optional[str]]:
    """
    Run individual Google site: searches for each danger term on the domain.

    Replaces the single complex OR query (which fails silently through the
    Bright Data SERP API) with N simple queries: site:{domain} {term}.
    Results are deduplicated by URL.
    """
    all_results: list[dict] = []
    seen_urls: set[str] = set()
    errors: list[str] = []
    success_count = 0

    for term in _SERP_PORN_GAMBLING_TERMS:
        query = f"site:{domain} {term}"
        results, err = serp_search(query, num_results=5)
        if err:
            errors.append(err)
            logger.warning("SERP query failed [%s]: %s", query, err)
        else:
            success_count += 1
        for r in results:
            url = r.get("url", "")
            # Only keep URLs that actually belong to the target domain
            if not url or url in seen_urls:
                continue
            from urllib.parse import urlparse as _up
            parsed_host = _up(url).netloc.lower().lstrip("www.")
            target_norm = domain.lower().lstrip("www.")
            if not (parsed_host == target_norm or parsed_host.endswith("." + target_norm)):
                logger.debug("SERP result %s does not belong to %s — skipping", url, domain)
                continue
            seen_urls.add(url)
            r["matched_term"] = term
            all_results.append(r)

    # Only surface an error if every single query failed
    combined_error = errors[0] if errors and success_count == 0 else None
    return all_results, combined_error


def site_search_core_keywords(domain: str, sample_keywords: list[str]) -> tuple[list[dict], Optional[str]]:
    """
    Check whether any Bright Data core pages appear in Google for this domain.
    Searches for the top few keywords combined with site:{domain}.
    """
    if not sample_keywords:
        return [], None
    kw_part = " OR ".join(f'"{kw}"' for kw in sample_keywords[:3])
    query = f"site:{domain} ({kw_part})"
    return serp_search(query, num_results=10)

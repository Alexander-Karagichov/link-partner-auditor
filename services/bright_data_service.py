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
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def _post_unlocker(payload: dict) -> requests.Response:
    """
    POST to the Web Unlocker endpoint with a generous timeout and automatic
    retries on read-timeouts / connection errors — the failure mode we see when
    many audits run concurrently. 4xx/5xx are not retried (they raise).
    """
    last_exc: Optional[Exception] = None
    for attempt in range(settings.BRIGHTDATA_MAX_RETRIES + 1):
        try:
            resp = requests.post(
                settings.BRIGHTDATA_UNLOCKER_ENDPOINT,
                headers=_auth_headers(),
                json=payload,
                timeout=settings.BRIGHTDATA_TIMEOUT,
            )
            resp.raise_for_status()
            return resp
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            logger.warning(
                "Bright Data network issue (attempt %d/%d) for %s: %s",
                attempt + 1, settings.BRIGHTDATA_MAX_RETRIES + 1,
                payload.get("url", "?"), exc.__class__.__name__,
            )
            continue
    raise last_exc  # type: ignore[misc] — retries exhausted


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
        resp = _post_unlocker(payload)

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
        resp = _post_unlocker(payload)
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

# Default SERP check terms — used ONLY if keywords/serp_porn_gambling_keywords.txt is
# missing/empty. Simple single-word queries work more reliably than complex
# OR/parenthesis expressions with Bright Data's SERP API.
_DEFAULT_SERP_TERMS = [
    "casino", "gambling", "porn", "sex", "xxx",
    "betting", "slots", "escort", "blackjack", "poker",
]

_serp_terms_cache: Optional[list[str]] = None


def _load_serp_terms() -> list[str]:
    """
    Load SERP check terms from keywords/serp_porn_gambling_keywords.txt (single-word,
    non-comment lines), capped at SERP_MAX_TERMS. Falls back to the default list
    if the file is missing/empty. Cached after first load.
    """
    global _serp_terms_cache
    if _serp_terms_cache is not None:
        return _serp_terms_cache
    terms: list[str] = []
    try:
        path = settings.SERP_CHECK_TERMS_FILE
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip().lower()
                if line and not line.startswith("#") and " " not in line:
                    terms.append(line)
    except Exception as exc:
        logger.warning("Could not read SERP terms file: %s", exc)
    if not terms:
        terms = list(_DEFAULT_SERP_TERMS)
    _serp_terms_cache = terms[: settings.SERP_MAX_TERMS]
    return _serp_terms_cache


_core_serp_terms_cache: Optional[list[str]] = None


def _load_core_serp_terms() -> list[str]:
    """
    Load core-business SERP terms from keywords/serp_core_business_keywords.txt
    (non-comment lines; multi-word phrases allowed), capped at SERP_MAX_TERMS.
    Cached after first load.
    """
    global _core_serp_terms_cache
    if _core_serp_terms_cache is not None:
        return _core_serp_terms_cache
    terms: list[str] = []
    try:
        path = settings.SERP_CORE_KEYWORDS_FILE
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip().lower()
                if line and not line.startswith("#"):
                    terms.append(line)
    except Exception as exc:
        logger.warning("Could not read core SERP terms file: %s", exc)
    _core_serp_terms_cache = terms[: settings.SERP_MAX_TERMS]
    return _core_serp_terms_cache


def reload_serp_terms() -> None:
    """Clear cached SERP term lists so the files are re-read after an edit."""
    global _serp_terms_cache, _core_serp_terms_cache
    _serp_terms_cache = None
    _core_serp_terms_cache = None


def _site_search_terms(domain: str, terms: list[str]) -> tuple[list[dict], Optional[str]]:
    """
    Run an individual Google "site:<domain> <term>" search for each term and
    return result URLs that belong to the domain (deduplicated). Multi-word
    terms are quoted as exact phrases. Each entry carries `matched_term`.
    """
    from urllib.parse import urlparse as _up

    if not terms:
        return [], None
    all_results: list[dict] = []
    seen_urls: set[str] = set()
    errors: list[str] = []
    success_count = 0
    target_norm = domain.lower().removeprefix("www.")

    def _run_term(term: str) -> tuple[str, list[dict], Optional[str]]:
        q = f'"{term}"' if " " in term else term  # quote multi-word phrases
        results, err = serp_search(f"site:{domain} {q}", num_results=5)
        return term, results, err

    # Concurrent, capped by INNER_CONCURRENCY to avoid overloading Bright Data.
    with ThreadPoolExecutor(max_workers=max(1, min(settings.INNER_CONCURRENCY, len(terms)))) as pool:
        futures = [pool.submit(_run_term, term) for term in terms]
        term_outputs = [f.result() for f in as_completed(futures)]

    for term, results, err in term_outputs:
        if err:
            errors.append(err)
            logger.warning("SERP query failed [site:%s %s]: %s", domain, term, err)
        else:
            success_count += 1
        for r in results:
            url = r.get("url", "")
            if not url or url in seen_urls:
                continue
            parsed_host = _up(url).netloc.lower().removeprefix("www.")
            if not (parsed_host == target_norm or parsed_host.endswith("." + target_norm)):
                continue
            seen_urls.add(url)
            r["matched_term"] = term
            all_results.append(r)

    combined_error = errors[0] if errors and success_count == 0 else None
    return all_results, combined_error


def site_search_porn_gambling(domain: str) -> tuple[list[dict], Optional[str]]:
    """Google site: checks for the danger-term list (serp_porn_gambling_keywords.txt)."""
    return _site_search_terms(domain, _load_serp_terms())


def site_search_core(domain: str) -> tuple[list[dict], Optional[str]]:
    """Google site: checks for the core-business list (serp_core_business_keywords.txt)."""
    return _site_search_terms(domain, _load_core_serp_terms())

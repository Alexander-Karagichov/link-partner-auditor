"""
Provider-agnostic scraping service — the single seam the audit engine talks to
(`from services import scraper_service as bdata`).

A scraper backend only has to implement TWO primitives:

    scrape_page(url) -> (html, error)
        Fetch the full HTML of a URL. On success error is None; on failure html
        is None and error is a short message.

    serp_search(query, num_results) -> (list[dict], error)
        Run a Google search and return result dicts with keys:
        position, title, url, snippet. On failure return ([], error).

`settings.SCRAPER_PROVIDER` selects the backend at startup:

    SCRAPER_PROVIDER=brightdata   -> services.bright_data_service     (default)
    SCRAPER_PROVIDER=requests     -> services.requests_scraper_service (example)

Everything else in this module (site: searches, term loading, domain filtering)
is provider-agnostic and built on top of those two primitives, so a new scraper
never has to reimplement it. Switching providers requires a restart.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

# ── Backend selection ──────────────────────────────────────────────────────────

if settings.SCRAPER_PROVIDER == "requests":
    from services import requests_scraper_service as _backend
else:
    from services import bright_data_service as _backend

# Re-export the two primitives the backend implements.
scrape_page = _backend.scrape_page
serp_search = _backend.serp_search


# ── Convenience wrappers for site: searches (provider-agnostic) ─────────────────

# Default SERP check terms — used ONLY if keywords/serp_porn_gambling_keywords.txt
# is missing/empty. Simple single-word queries work more reliably than complex
# OR/parenthesis expressions.
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

    # Concurrent, capped by INNER_CONCURRENCY to avoid overloading the scraper.
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

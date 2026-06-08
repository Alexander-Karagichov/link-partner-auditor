"""
SEMrush API service.

Covers:
  - Domain overview  (authority score, organic traffic)
  - Backlinks overview  (referring domains, total backlinks)
  - Organic keyword rankings  (top N keywords with positions)
  - AI Overview data  (visibility score, mentions, cited pages)
    – requires SEMrush .Trends / AI Toolkit plan; returns None values if unavailable
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

import requests
from urllib.parse import quote

from config import settings

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update({"Accept": "text/plain"})


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class DomainOverview:
    domain: str
    authority_score: Optional[int] = None
    organic_traffic: Optional[int] = None
    organic_keywords: Optional[int] = None
    paid_keywords: Optional[int] = None
    error: Optional[str] = None


@dataclass
class BacklinksOverview:
    domain: str
    total_backlinks: Optional[int] = None
    referring_domains: Optional[int] = None
    referring_ips: Optional[int] = None
    follow_links: Optional[int] = None
    nofollow_links: Optional[int] = None
    authority_score: Optional[int] = None
    error: Optional[str] = None


@dataclass
class OrganicKeyword:
    phrase: str
    position: int
    search_volume: Optional[int] = None
    url: Optional[str] = None


@dataclass
class OrganicRankings:
    domain: str
    keywords: list[OrganicKeyword] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class AIOverviewData:
    domain: str
    ai_visibility_score: Optional[float] = None   # 0–100
    ai_mentions: Optional[int] = None
    ai_cited_pages: Optional[int] = None
    error: Optional[str] = None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _api_key() -> str:
    if not settings.SEMRUSH_API_KEY:
        raise ValueError("SEMRUSH_API_KEY is not configured.")
    return settings.SEMRUSH_API_KEY


def _get(base: str, params: dict) -> requests.Response:
    """
    Make a GET request to the SEMrush API.

    Commas in values (e.g. export_columns) are kept unencoded because
    SEMrush requires literal commas – standard urlencode() breaks them.
    """
    parts = []
    for k, v in params.items():
        parts.append(f"{quote(str(k), safe='')}={quote(str(v), safe=',')}")
    url = f"{base}?{'&'.join(parts)}"
    resp = _SESSION.get(url, timeout=settings.REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp


def _parse_kv_response(text: str) -> dict[str, str]:
    """Parse SEMrush single-row responses (header line + data line)."""
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return {}
    headers = lines[0].split(";")
    values = lines[1].split(";")
    return dict(zip(headers, values))


def _parse_tabular_response(text: str) -> list[dict[str, str]]:
    """Parse SEMrush multi-row responses (header + N data rows)."""
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return []
    headers = lines[0].split(";")
    rows = []
    for line in lines[1:]:
        values = line.split(";")
        rows.append(dict(zip(headers, values)))
    return rows


def _safe_int(value: str | None) -> Optional[int]:
    try:
        return int(value) if value not in (None, "", "N/A") else None
    except (ValueError, TypeError):
        return None


def _safe_float(value: str | None) -> Optional[float]:
    try:
        return float(value) if value not in (None, "", "N/A") else None
    except (ValueError, TypeError):
        return None


def _clean_domain(domain: str) -> str:
    """Strip protocol and trailing slash from a domain string."""
    domain = domain.strip().lower()
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    return domain.rstrip("/").split("/")[0]  # drop any path


# ── Public API functions ───────────────────────────────────────────────────────

# SEMrush's "worldwide" organic traffic in the UI is a sum of their major
# country databases. There is no single worldwide endpoint, so we query each
# major database in parallel and sum the results.
_SEMRUSH_MAJOR_DATABASES = [
    "us", "uk", "ca", "au", "in", "ph", "br", "de", "my", "sg",
    "id", "nl", "za", "ng", "mx", "fr", "ru", "tr", "pl", "cl",
    "th", "ar", "kr", "jp", "es", "it", "vn", "co", "eg", "ve",
    "pe", "nz", "gr", "se", "no", "dk", "fi", "be", "at", "ch",
    "cz", "hu", "ro", "ua", "il", "ae", "sa", "pk",
]


def _fetch_db_traffic(domain: str, db: str) -> tuple[int, int]:
    """Fetch (organic_traffic, organic_keywords) for one database. Returns (0,0) on error."""
    try:
        params = {
            "type": "domain_ranks",
            "key": _api_key(),
            "domain": domain,
            "database": db,
            "export_columns": "Dn,Or,Ot",
        }
        resp = _get(settings.SEMRUSH_API_BASE + "/", params)
        data = _parse_kv_response(resp.text)
        traffic = _safe_int(data.get("Organic Traffic") or data.get("Ot")) or 0
        keywords = _safe_int(data.get("Organic Keywords") or data.get("Or")) or 0
        return traffic, keywords
    except Exception:
        return 0, 0


def get_domain_overview(domain: str) -> DomainOverview:
    """
    Fetch domain-level SEO metrics from SEMrush.

    Queries all major country databases in parallel and sums the results
    to approximate SEMrush's worldwide organic traffic figure.
    """
    domain = _clean_domain(domain)
    result = DomainOverview(domain=domain)
    try:
        total_traffic = 0
        total_keywords = 0
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = {pool.submit(_fetch_db_traffic, domain, db): db for db in _SEMRUSH_MAJOR_DATABASES}
            for future in as_completed(futures):
                t, k = future.result()
                total_traffic += t
                total_keywords += k
        result.organic_traffic = total_traffic if total_traffic else None
        result.organic_keywords = total_keywords if total_keywords else None
    except Exception as exc:
        result.error = str(exc)
        logger.exception("Unexpected error fetching domain overview for %s", domain)
    return result


def get_backlinks_overview(domain: str) -> BacklinksOverview:
    """
    Fetch backlink metrics from SEMrush.

    Uses 'backlinks_overview' report via the analytics endpoint.
    Returns total backlinks, referring domains, referring IPs,
    follow / nofollow counts.
    """
    domain = _clean_domain(domain)
    result = BacklinksOverview(domain=domain)
    try:
        params = {
            "key": _api_key(),
            "type": "backlinks_overview",
            "target": domain,
            "target_type": "root_domain",
            "export_columns": "total,domains_num,urls_num,ips_num,follows_num,nofollows_num,ascore",
        }
        resp = _get(settings.SEMRUSH_API_BASE + "/analytics/v1/", params)
        data = _parse_kv_response(resp.text)
        result.total_backlinks = _safe_int(data.get("total"))
        result.referring_domains = _safe_int(data.get("domains_num"))
        result.referring_ips = _safe_int(data.get("ips_num"))
        result.follow_links = _safe_int(data.get("follows_num"))
        result.nofollow_links = _safe_int(data.get("nofollows_num"))
        result.authority_score = _safe_int(data.get("ascore"))
    except requests.HTTPError as exc:
        result.error = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        logger.warning("SEMrush backlinks error for %s: %s", domain, result.error)
    except Exception as exc:
        result.error = str(exc)
        logger.exception("Unexpected error fetching backlinks for %s", domain)
    return result


def get_organic_rankings(domain: str, limit: int = 100) -> OrganicRankings:
    """
    Fetch the top organic keyword rankings for a domain.

    Returns a list of OrganicKeyword objects sorted by position ascending.
    """
    domain = _clean_domain(domain)
    result = OrganicRankings(domain=domain)
    try:
        params = {
            "type": "domain_organic",
            "key": _api_key(),
            "domain": domain,
            "database": "us",
            "display_limit": limit,
            "display_sort": "po_asc",
            "export_columns": "Ph,Po,Nq,Ur",
        }
        resp = _get(settings.SEMRUSH_API_BASE + "/", params)
        rows = _parse_tabular_response(resp.text)
        for row in rows:
            kw = OrganicKeyword(
                phrase=row.get("Keyword", row.get("Ph", "")),
                position=_safe_int(row.get("Position", row.get("Po"))) or 0,
                search_volume=_safe_int(row.get("Search Volume", row.get("Nq"))),
                # SEMrush returns the column header as "Url" (title-case)
                url=row.get("Url", row.get("URL", row.get("Ur", ""))),
            )
            result.keywords.append(kw)
    except requests.HTTPError as exc:
        result.error = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        logger.warning("SEMrush organic error for %s: %s", domain, result.error)
    except Exception as exc:
        result.error = str(exc)
        logger.exception("Unexpected error fetching organic rankings for %s", domain)
    return result


def get_organic_keywords_for_terms(domain: str, danger_terms: list[str], limit_per_term: int = 50) -> list[OrganicKeyword]:
    """
    Run one SEMrush domain_organic query per danger term using display_filter.

    Unlike get_organic_rankings() which only returns the top-N keywords by
    position, this targets specific terms regardless of how many keywords the
    domain ranks for overall. Catches casino/gambling terms buried beyond
    position 50 for large domains.

    Uses requests params= dict so | and + are percent-encoded correctly.
    """
    domain = _clean_domain(domain)
    seen: set[str] = set()
    results: list[OrganicKeyword] = []

    for term in danger_terms:
        term = term.strip()
        # Skip multi-word terms – spaces in the filter value break SEMrush's
        # display_filter and cause it to return unrelated top keywords instead.
        # Multi-word terms are still covered by get_organic_rankings + _keywords_found_in_rankings.
        if not term or " " in term:
            continue
        try:
            # Build URL manually so | in display_filter is preserved.
            # + must be encoded as %2B (raw + means space in query strings).
            _safe_val = lambda v: quote(str(v), safe=',|')
            parts = [
                f"type=domain_organic",
                f"key={_api_key()}",
                f"domain={quote(domain, safe='')}",
                f"database=us",
                f"display_limit={limit_per_term}",
                f"display_sort=po_asc",
                f"export_columns=Ph,Po,Nq,Ur",
                f"display_filter={_safe_val(f'+|Ph|Co|{term}')}",
            ]
            url = settings.SEMRUSH_API_BASE + "/?" + "&".join(parts)
            resp = _SESSION.get(url, timeout=settings.REQUEST_TIMEOUT)
            resp.raise_for_status()
            rows = _parse_tabular_response(resp.text)
            for row in rows:
                phrase = row.get("Keyword", row.get("Ph", ""))
                if not phrase or phrase.lower() in seen:
                    continue
                seen.add(phrase.lower())
                # SEMrush returns the column as "Url" (title-case) in this report
                url_val = row.get("Url", row.get("URL", row.get("Ur", "")))
                results.append(OrganicKeyword(
                    phrase=phrase,
                    position=_safe_int(row.get("Position", row.get("Po"))) or 0,
                    search_volume=_safe_int(row.get("Search Volume", row.get("Nq"))),
                    url=url_val,
                ))
        except requests.HTTPError as exc:
            logger.warning(
                "SEMrush filtered organic [%s / filter:%s]: HTTP %d – %s",
                domain, term, exc.response.status_code, exc.response.text[:100],
            )
        except Exception as exc:
            logger.warning("SEMrush filtered organic [%s / filter:%s]: %s", domain, term, exc)

    return results


def get_ai_overview_data(domain: str) -> AIOverviewData:
    """
    Attempt to fetch AI Search metrics from SEMrush.

    These metrics (AI Visibility, Mentions, Cited Pages) are available through
    SEMrush's AI Toolkit / .Trends subscription.  The function degrades
    gracefully – returning None values – if the endpoint is unavailable or the
    account does not have access.
    """
    domain = _clean_domain(domain)
    result = AIOverviewData(domain=domain)
    try:
        # SEMrush AI Overviews API (requires .Trends / AI Toolkit plan)
        params = {
            "key": _api_key(),
            "type": "ai_overviews_domain",
            "target": domain,
            "target_type": "root_domain",
            "database": "us",
        }
        resp = _get(settings.SEMRUSH_API_BASE + "/", params)
        data = _parse_kv_response(resp.text)
        result.ai_visibility_score = _safe_float(data.get("visibility_score", data.get("Vs")))
        result.ai_mentions = _safe_int(data.get("mentions", data.get("Mn")))
        result.ai_cited_pages = _safe_int(data.get("cited_pages", data.get("Cp")))
    except requests.HTTPError as exc:
        # 400/403/404 are expected when the plan does not include AI data
        code = exc.response.status_code
        if code in (400, 403, 404):
            result.error = f"AI Overview data not available (HTTP {code}) – requires SEMrush .Trends plan"
        else:
            result.error = f"HTTP {code}: {exc.response.text[:200]}"
        logger.info("SEMrush AI overview not available for %s: %s", domain, result.error)
    except Exception as exc:
        result.error = str(exc)
        logger.exception("Unexpected error fetching AI overview for %s", domain)
    return result

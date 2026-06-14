"""
DataForSEO backend — a drop-in alternative to SEMrush.

Implements the shared SEO interface (services/seo_models.py) using the
DataForSEO v3 REST API:
  - Domain overview  : POST /v3/dataforseo_labs/google/domain_rank_overview/live
  - Backlinks        : POST /v3/backlinks/summary/live
  - Organic rankings : POST /v3/dataforseo_labs/google/ranked_keywords/live

Auth: HTTP Basic with your DataForSEO login + password (app.dataforseo.com/api-access).
Requests are a JSON array of task objects; a top-level/per-task status_code of
20000 means success.

NOTE: This adapter is written to the published DataForSEO docs but should be
verified against a live account — field nesting can vary by plan/endpoint, so
the parsers below dig defensively for the values they need.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests

from config import settings
from services.seo_models import (
    DomainOverview,
    BacklinksOverview,
    OrganicKeyword,
    OrganicRankings,
)

logger = logging.getLogger(__name__)

_BASE = "https://api.dataforseo.com"
_OK = 20000  # DataForSEO success status code

_SESSION = requests.Session()
_ADAPTER = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=64, max_retries=2)
_SESSION.mount("https://", _ADAPTER)
_SESSION.mount("http://", _ADAPTER)


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _auth() -> tuple[str, str]:
    if not settings.DATAFORSEO_LOGIN or not settings.DATAFORSEO_PASSWORD:
        raise ValueError(
            "DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD are not configured. "
            "Get them at https://app.dataforseo.com/api-access"
        )
    return settings.DATAFORSEO_LOGIN, settings.DATAFORSEO_PASSWORD


def _clean_domain(domain: str) -> str:
    domain = domain.strip().lower()
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    return domain.rstrip("/").split("/")[0]


def _request(endpoint: str, task: dict) -> tuple[Optional[list], Optional[str]]:
    """POST a single task and return (result_list, error)."""
    resp = _SESSION.post(_BASE + endpoint, json=[task], auth=_auth(), timeout=settings.REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status_code") != _OK:
        return None, f"DataForSEO error {data.get('status_code')}: {data.get('status_message')}"
    tasks = data.get("tasks") or []
    if not tasks:
        return None, "DataForSEO returned no tasks"
    t = tasks[0]
    if t.get("status_code") != _OK:
        return None, f"DataForSEO task error {t.get('status_code')}: {t.get('status_message')}"
    return (t.get("result") or []), None


def _common_params(domain: str) -> dict:
    return {
        "target": domain,
        "location_code": settings.DATAFORSEO_LOCATION_CODE,
        "language_name": settings.DATAFORSEO_LANGUAGE,
    }


def _dig_organic_metrics(result_list: Optional[list]) -> Optional[dict]:
    """Find metrics.organic in a domain_rank_overview result (shape-tolerant)."""
    for r in result_list or []:
        for it in (r.get("items") or []):
            m = (it.get("metrics") or {}).get("organic")
            if m:
                return m
        m = (r.get("metrics") or {}).get("organic")
        if m:
            return m
    return None


def _parse_ranked(result_list: Optional[list]) -> list[OrganicKeyword]:
    out: list[OrganicKeyword] = []
    for r in result_list or []:
        for it in (r.get("items") or []):
            kd = it.get("keyword_data") or {}
            serp = (it.get("ranked_serp_element") or {}).get("serp_item") or {}
            phrase = kd.get("keyword")
            if not phrase:
                continue
            out.append(OrganicKeyword(
                phrase=phrase,
                position=serp.get("rank_group") or serp.get("rank_absolute") or 0,
                search_volume=(kd.get("keyword_info") or {}).get("search_volume"),
                url=serp.get("url") or serp.get("relative_url"),
            ))
    return out


# ── Public API ───────────────────────────────────────────────────────────────

def get_domain_overview(domain: str) -> DomainOverview:
    domain = _clean_domain(domain)
    result = DomainOverview(domain=domain)
    try:
        res, err = _request(
            "/v3/dataforseo_labs/google/domain_rank_overview/live", _common_params(domain)
        )
        if err:
            result.error = err
            return result
        organic = _dig_organic_metrics(res)
        if organic:
            etv = organic.get("etv")
            result.organic_traffic = int(round(etv)) if isinstance(etv, (int, float)) else None
            result.organic_keywords = organic.get("count")
            result.paid_keywords = organic.get("paid_keywords")
    except requests.HTTPError as exc:
        result.error = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        logger.warning("DataForSEO domain overview error for %s: %s", domain, result.error)
    except Exception as exc:
        result.error = str(exc)
        logger.exception("Unexpected error fetching DataForSEO domain overview for %s", domain)
    return result


def get_backlinks_overview(domain: str) -> BacklinksOverview:
    domain = _clean_domain(domain)
    result = BacklinksOverview(domain=domain)
    try:
        task = {
            "target": domain,
            "internal_list_limit": 10,
            "backlinks_status_type": "live",
            "include_subdomains": True,
        }
        res, err = _request("/v3/backlinks/summary/live", task)
        if err:
            result.error = err
            return result
        if res:
            d = res[0]
            result.total_backlinks = d.get("backlinks")
            result.referring_domains = d.get("referring_domains")
            result.referring_ips = d.get("referring_ips")
            # DataForSEO backlink rank is 0-1000; normalise to 0-100 like SEMrush AS.
            rank = d.get("rank")
            result.authority_score = int(round(rank / 10)) if isinstance(rank, (int, float)) else None
    except requests.HTTPError as exc:
        result.error = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        logger.warning("DataForSEO backlinks error for %s: %s", domain, result.error)
    except Exception as exc:
        result.error = str(exc)
        logger.exception("Unexpected error fetching DataForSEO backlinks for %s", domain)
    return result


def get_organic_rankings(domain: str, limit: int = 100, databases: Optional[list[str]] = None) -> OrganicRankings:
    # `databases` is accepted for interface parity with SEMrush; DataForSEO uses
    # the configured DATAFORSEO_LOCATION_CODE instead of per-country DBs.
    domain = _clean_domain(domain)
    result = OrganicRankings(domain=domain)
    try:
        task = {
            **_common_params(domain),
            "limit": min(limit, 1000),
            "order_by": ["ranked_serp_element.serp_item.rank_group,asc"],
            "filters": [["ranked_serp_element.serp_item.is_paid", "=", False]],
        }
        res, err = _request("/v3/dataforseo_labs/google/ranked_keywords/live", task)
        if err:
            result.error = err
            return result
        result.keywords = _parse_ranked(res)
    except requests.HTTPError as exc:
        result.error = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        logger.warning("DataForSEO ranked keywords error for %s: %s", domain, result.error)
    except Exception as exc:
        result.error = str(exc)
        logger.exception("Unexpected error fetching DataForSEO rankings for %s", domain)
    return result


def get_top_traffic_pages(domain: str, database: str = "us", limit: int = 10) -> list:
    """
    Parity stub for the SEO interface. The content-farm trivia check (Check 1) is
    SEMrush-specific; under the DataForSEO provider it is simply skipped (returns
    no pages), so the content-farm score relies on the cheap homepage check only.
    """
    return []


def get_organic_keywords_for_terms(domain: str, danger_terms: list[str], limit_per_term: int = 50, databases: Optional[list[str]] = None) -> list[OrganicKeyword]:
    """One ranked_keywords query per term (filtered by keyword substring), concurrently.

    `databases` is accepted for interface parity with SEMrush; DataForSEO uses
    the configured DATAFORSEO_LOCATION_CODE instead.
    """
    domain = _clean_domain(domain)
    terms = [t.strip() for t in danger_terms if t and t.strip() and " " not in t.strip()]
    if not terms:
        return []

    def _one(term: str) -> list[OrganicKeyword]:
        task = {
            **_common_params(domain),
            "limit": limit_per_term,
            "filters": [
                ["keyword_data.keyword", "like", f"%{term}%"],
                "and",
                ["ranked_serp_element.serp_item.is_paid", "=", False],
            ],
            "order_by": ["ranked_serp_element.serp_item.rank_group,asc"],
        }
        res, err = _request("/v3/dataforseo_labs/google/ranked_keywords/live", task)
        if err:
            logger.warning("DataForSEO filtered [%s / %s]: %s", domain, term, err)
            return []
        return _parse_ranked(res)

    seen: set[str] = set()
    results: list[OrganicKeyword] = []
    with ThreadPoolExecutor(max_workers=min(settings.INNER_CONCURRENCY, len(terms))) as pool:
        for fut in as_completed([pool.submit(_one, t) for t in terms]):
            for kw in fut.result():
                if kw.phrase.lower() in seen:
                    continue
                seen.add(kw.phrase.lower())
                results.append(kw)
    return results

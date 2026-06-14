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
# Larger connection pool so concurrent audits don't exhaust it. The default
# pool_maxsize=10 caused "Connection pool is full, discarding connection"
# churn (and wasted reconnects) under parallel load.
_ADAPTER = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=64, max_retries=2)
_SESSION.mount("https://", _ADAPTER)
_SESSION.mount("http://", _ADAPTER)


# ── Data models ───────────────────────────────────────────────────────────────
# Shared models live in seo_models so every SEO provider returns identical types.
from services.seo_models import (  # noqa: E402
    DomainOverview,
    BacklinksOverview,
    OrganicKeyword,
    OrganicRankings,
)


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

def get_domain_overview(domain: str) -> DomainOverview:
    """
    Fetch domain-level SEO metrics from SEMrush in a SINGLE all-databases call.

    `type=domain_ranks` with NO `database` parameter returns one row per regional
    database the domain ranks in. We sum organic traffic/keywords across rows for
    a worldwide figure, and expose the per-country breakdown + the top databases
    by traffic — used to target the deeper ranking checks at the site's real
    markets instead of a hardcoded US default. One request replaces the old
    48-call sweep (much faster, no connection-pool thrash).
    """
    domain = _clean_domain(domain)
    result = DomainOverview(domain=domain)
    try:
        params = {
            "type": "domain_ranks",
            "key": _api_key(),
            "domain": domain,
            "export_columns": "Db,Dn,Or,Ot",
        }
        resp = _get(settings.SEMRUSH_API_BASE + "/", params)
        rows = _parse_tabular_response(resp.text)

        total_traffic = 0
        total_keywords = 0
        by_country: dict[str, int] = {}
        for row in rows:
            db = (row.get("Database") or row.get("Db") or "").strip()
            ot = _safe_int(row.get("Organic Traffic") or row.get("Ot")) or 0
            kw = _safe_int(row.get("Organic Keywords") or row.get("Or")) or 0
            if db:
                by_country[db] = by_country.get(db, 0) + ot
            total_traffic += ot
            total_keywords += kw

        result.organic_traffic = total_traffic or None
        result.organic_keywords = total_keywords or None
        result.traffic_by_country = by_country
        n = max(1, settings.SEMRUSH_TOP_COUNTRIES) if settings.SEMRUSH_TOP_COUNTRIES else 0
        result.top_databases = [
            db for db, _ in sorted(by_country.items(), key=lambda kv: kv[1], reverse=True) if db
        ][:n] if n else []
    except requests.HTTPError as exc:
        result.error = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        logger.warning("SEMrush domain overview error for %s: %s", domain, result.error)
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


def _rankings_one_db(domain: str, database: str, limit: int) -> tuple[list[OrganicKeyword], Optional[str]]:
    """Fetch organic rankings for one database. Returns (keywords, error)."""
    out: list[OrganicKeyword] = []
    try:
        params = {
            "type": "domain_organic",
            "key": _api_key(),
            "domain": domain,
            "database": database,
            "display_limit": limit,
            "display_sort": "po_asc",
            "export_columns": "Ph,Po,Nq,Ur",
        }
        resp = _get(settings.SEMRUSH_API_BASE + "/", params)
        for row in _parse_tabular_response(resp.text):
            out.append(OrganicKeyword(
                phrase=row.get("Keyword", row.get("Ph", "")),
                position=_safe_int(row.get("Position", row.get("Po"))) or 0,
                search_volume=_safe_int(row.get("Search Volume", row.get("Nq"))),
                # SEMrush returns the column header as "Url" (title-case)
                url=row.get("Url", row.get("URL", row.get("Ur", ""))),
            ))
        return out, None
    except requests.HTTPError as exc:
        msg = f"HTTP {exc.response.status_code}: {exc.response.text[:150]}"
        logger.warning("SEMrush organic [%s/%s]: %s", domain, database, msg)
        return out, msg
    except Exception as exc:
        logger.warning("SEMrush organic [%s/%s]: %s", domain, database, exc)
        return out, str(exc)


def get_organic_rankings(domain: str, limit: int = 100, databases: Optional[list[str]] = None) -> OrganicRankings:
    """
    Fetch top organic keyword rankings across one or more country databases,
    merged (dedup by phrase, keeping the best position).

    `databases` defaults to ["us"]. Pass a domain's top traffic countries (from
    DomainOverview.top_databases) to audit the markets it actually ranks in.
    """
    domain = _clean_domain(domain)
    result = OrganicRankings(domain=domain)
    dbs = [d for d in (databases or ["us"]) if d] or ["us"]

    merged: dict[str, OrganicKeyword] = {}
    first_error: Optional[str] = None
    with ThreadPoolExecutor(max_workers=min(settings.INNER_CONCURRENCY, len(dbs))) as pool:
        for kws, err in pool.map(lambda db: _rankings_one_db(domain, db, limit), dbs):
            if err and first_error is None:
                first_error = err
            for kw in kws:
                key = (kw.phrase or "").lower()
                if not key:
                    continue
                prev = merged.get(key)
                if prev is None or (kw.position and kw.position < (prev.position or 10**9)):
                    merged[key] = kw

    result.keywords = list(merged.values())
    if not result.keywords and first_error:  # only surface an error if we got nothing
        result.error = first_error
    return result


def _filtered_query_one(domain: str, term: str, limit_per_term: int, database: str = "us") -> list[OrganicKeyword]:
    """Run a single SEMrush display_filter query for one danger term in one database."""
    out: list[OrganicKeyword] = []
    try:
        # Build URL manually so | in display_filter is preserved.
        # + must be encoded as %2B (raw + means space in query strings).
        _safe_val = lambda v: quote(str(v), safe=',|')
        parts = [
            f"type=domain_organic",
            f"key={_api_key()}",
            f"domain={quote(domain, safe='')}",
            f"database={database}",
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
            if not phrase:
                continue
            # SEMrush returns the column as "Url" (title-case) in this report
            url_val = row.get("Url", row.get("URL", row.get("Ur", "")))
            out.append(OrganicKeyword(
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
    return out


def get_organic_keywords_for_terms(domain: str, danger_terms: list[str], limit_per_term: int = 50, databases: Optional[list[str]] = None) -> list[OrganicKeyword]:
    """
    Run one SEMrush domain_organic display_filter query per (database, term),
    concurrently, then merge the results (dedup by phrase).

    Targets specific danger terms regardless of overall ranking count — catches
    casino/gambling terms buried beyond position 50. `databases` defaults to
    ["us"]; pass the domain's top traffic countries to check its real markets.
    """
    domain = _clean_domain(domain)

    # Multi-word phrases ARE supported — the filter value is URL-encoded (space
    # -> %20), so "residential proxy" works and comes back with its position.
    terms = [t.strip() for t in danger_terms if t.strip()]
    if not terms:
        return []
    dbs = [d for d in (databases or ["us"]) if d] or ["us"]
    jobs = [(t, db) for db in dbs for t in terms]

    seen: set[str] = set()
    results: list[OrganicKeyword] = []
    with ThreadPoolExecutor(max_workers=min(settings.INNER_CONCURRENCY, len(jobs))) as pool:
        futures = [pool.submit(_filtered_query_one, domain, t, limit_per_term, db) for (t, db) in jobs]
        for fut in as_completed(futures):
            for kw in fut.result():
                if kw.phrase.lower() in seen:
                    continue
                seen.add(kw.phrase.lower())
                results.append(kw)

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

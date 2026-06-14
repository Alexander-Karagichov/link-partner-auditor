"""
Audit engine – main orchestration layer.

`audit_domain(url)` runs ALL checks for a single domain and returns a
comprehensive AuditResult dataclass.

`audit_bulk(urls, progress_callback)` processes a list of domains using a
thread pool (MAX_CONCURRENT_AUDITS at a time) and calls an optional callback
after each domain completes so the UI can show progress.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from config import settings
from services import seo_service as semrush
from services import bright_data_service as bdata
from services import link_checker_service as link_checker
from services import llm_service as ai_service
from services import pbn_service

logger = logging.getLogger(__name__)


# ── Keyword loaders (cached) ───────────────────────────────────────────────────

_core_keywords: list[str] = []
_porn_gambling_keywords: list[str] = []
_linkbuilding_targets: list[dict] = []


def _load_keywords(path: Path) -> list[str]:
    if not path.exists():
        logger.warning("Keyword file not found: %s", path)
        return []
    words = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().lower()
        if line and not line.startswith("#"):
            words.append(line)
    return words


def get_core_keywords() -> list[str]:
    global _core_keywords
    if not _core_keywords:
        _core_keywords = _load_keywords(settings.SEMRUSH_CORE_KEYWORDS_FILE)
    return _core_keywords


def get_porn_gambling_keywords() -> list[str]:
    global _porn_gambling_keywords
    if not _porn_gambling_keywords:
        _porn_gambling_keywords = _load_keywords(settings.PORN_GAMBLING_KEYWORDS_FILE)
    return _porn_gambling_keywords


def _load_targets(path: Path) -> list[dict]:
    """Parse a 'Keyword - URL' file into a list of {keyword, url} dicts."""
    if not path.exists():
        logger.warning("Link-building targets file not found: %s", path)
        return []
    targets = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if " - " not in line:
            logger.warning("Skipping malformed linkbuilding targets line: %r", line)
            continue
        keyword, _, url = line.partition(" - ")
        keyword = keyword.strip()
        url = url.strip()
        if keyword and url:
            targets.append({"keyword": keyword, "url": url})
    return targets


def get_linkbuilding_targets() -> list[dict]:
    global _linkbuilding_targets
    if not _linkbuilding_targets:
        _linkbuilding_targets = _load_targets(settings.LINKBUILDING_TARGETS_FILE)
    return _linkbuilding_targets


def reload_keywords() -> None:
    """Force reload of all keyword lists, link-building targets, and SERP terms."""
    global _core_keywords, _porn_gambling_keywords, _linkbuilding_targets
    _core_keywords = []
    _porn_gambling_keywords = []
    _linkbuilding_targets = []
    bdata.reload_serp_terms()
    from services import outbound_classifier as oc
    oc.reload_legit_domains()


def _homepage_gambling_gate(domain: str, html: str) -> tuple[bool, list[dict], Optional[str], dict, list[dict]]:
    """
    Decide if the homepage links DIRECTLY to gambling/porn — an instant fail.
    Returns (failed, offending_links, reason, buckets, verdicts).

    `buckets` is the outbound_classifier.classify_outbound() result and `verdicts`
    is the AI classify_link_partners() result — returned so the caller can reuse
    them for the strange/legit split instead of re-running the AI classifier.
    Two detectors: known_bad_sites.txt match (free) then, only if clean, the AI
    classifier on non-allowlisted outbound domains.
    """
    from services import outbound_classifier as oc

    offending: list[dict] = []

    # 1. Known-bad list match (free).
    link_result = link_checker.check_links(html, f"https://{domain}")
    for m in link_result.bad_link_matches:
        offending.append({
            "found_href": m.found_href,
            "matched_bad_domain": m.matched_bad_domain,
            "link_text": m.link_text,
        })
    if offending:
        return True, offending, "Homepage links to a known gambling/adult site.", {}, []

    # 2. AI classify non-allowlisted candidates (computed once, reused by caller).
    buckets = oc.classify_outbound(html, f"https://{domain}")
    candidates = buckets.get("candidates", [])
    verdicts = ai_service.classify_link_partners(f"https://{domain}", candidates) if candidates else []
    for v in verdicts:
        if v.get("category") == "gambling_porn":
            offending.append({
                "found_href": v["domain"],
                "matched_bad_domain": f"[AI: {v.get('reason', 'gambling/adult')}]",
                "link_text": "",
            })
    if offending:
        return True, offending, "Homepage links to an AI-detected gambling/adult site.", buckets, verdicts

    return False, [], None, buckets, verdicts


# ── Keyword matching helpers ───────────────────────────────────────────────────

def _danger_patterns(terms: list[str]) -> list:
    r"""Compile leading-word-boundary regexes (\bterm) for the given danger terms."""
    return [
        re.compile(r"\b" + re.escape(t.lower()))
        for t in terms
        if t and t.strip()
    ]


def _keywords_found_in_rankings(
    ranked_keywords: list[semrush.OrganicKeyword],
    target_keywords: list[str],
) -> list[dict]:
    r"""
    Return list of {keyword, position, url} where a danger term appears as a
    WHOLE WORD inside the domain's ranked phrase.

    Uses a leading word-boundary match (\bterm) so a term like "sex" does NOT
    match unrelated words it is a mid-word substring of ("Sussex", "Essex") and
    "bet" does not match "alphabet" — while still catching inflections like
    "casino" → "casinos" and "slot" → "slots". This removes the bulk of
    false-positive hits. Genuine whole-word business-name matches (e.g. "casino"
    in the taxi company "Casino Cab Co") still match, and are down-weighted later
    by the proportional PBN scoring and the AI's business/place-name context check.
    """
    patterns = _danger_patterns(target_keywords)
    seen: set[str] = set()
    unique: list[dict] = []
    for kw in ranked_keywords:
        phrase = (kw.phrase or "").lower()
        if not phrase or phrase in seen:
            continue
        if any(pat.search(phrase) for pat in patterns):
            seen.add(phrase)
            unique.append({"keyword": phrase, "position": kw.position, "url": kw.url or ""})
    return unique


# ── Result data model ──────────────────────────────────────────────────────────

@dataclass
class AuditResult:
    # ── Identity ──────────────────────────────────────────────────────────────
    domain: str
    input_url: str

    # ── SEO (SEMrush) ─────────────────────────────────────────────────────────
    authority_score: Optional[int] = None
    organic_traffic: Optional[int] = None
    referring_domains: Optional[int] = None
    total_backlinks: Optional[int] = None
    seo_error: Optional[str] = None
    backlinks_error: Optional[str] = None

    # ── Markets (top traffic countries) ───────────────────────────────────────
    top_countries: list[str] = field(default_factory=list)   # DB/locale codes, highest traffic first
    traffic_by_country: dict = field(default_factory=dict)   # {db_code: organic_traffic}

    # ── Organic Rankings ──────────────────────────────────────────────────────
    core_keyword_hits: list[dict] = field(default_factory=list)   # [{keyword, position, url}]
    porn_gambling_keyword_hits: list[dict] = field(default_factory=list)
    rankings_error: Optional[str] = None

    # ── Link Check ────────────────────────────────────────────────────────────
    homepage_scraped: bool = False
    total_links_on_page: int = 0
    bad_links_found: list[dict] = field(default_factory=list)     # [{found_href, matched_bad_domain, link_text}]
    competitor_links_found: list[dict] = field(default_factory=list)  # [{found_href, matched_competitor, link_text}]
    keyword_link_flags: list[str] = field(default_factory=list)   # secondary: keyword-in-link signals
    scrape_error: Optional[str] = None

    # ── Deep page link checks (ranking pages with porn/gambling hits) ──────────
    deep_page_checks: list[dict] = field(default_factory=list)    # [{page_url, keyword, bad_links, keyword_flags, total_links, error}]

    # ── Google SERP Verification ───────────────────────────────────────────────
    serp_porn_gambling_results: list[dict] = field(default_factory=list)
    serp_porn_gambling_error: Optional[str] = None
    serp_core_results: list[dict] = field(default_factory=list)   # core-business site: hits
    serp_core_error: Optional[str] = None

    # ── Early hard-fail (homepage gambling/porn gate) ─────────────────────────
    early_failed: bool = False
    early_fail_reason: Optional[str] = None

    # ── Outbound link analysis (PBN / link scheme) ────────────────────────────
    outbound_classification: dict = field(default_factory=dict)   # {own_entity, legit, strange}
    reciprocal_links: list[dict] = field(default_factory=list)    # [{partner, links_back, partner_legit}]
    business_legitimacy: dict = field(default_factory=dict)       # {is_legit, score, signals}

    # ── AI Analysis (OpenAI) ──────────────────────────────────────────────────
    ai_analysis: dict = field(default_factory=dict)
    ai_analysis_error: Optional[str] = None

    # ── Link Building Recommendation (OpenAI) ────────────────────────────────
    link_recommendation: dict = field(default_factory=dict)
    link_recommendation_error: Optional[str] = None

    # ── About page ─────────────────────────────────────────────────────────────
    about_page_text: Optional[str] = None   # extracted visible text from /about (or similar)
    homepage_text: Optional[str] = None      # extracted visible text from the homepage (niche context)

    # ── PBN / link-network ─────────────────────────────────────────────────────
    pbn: dict = field(default_factory=dict)

    # ── Overall Risk ──────────────────────────────────────────────────────────
    risk_level: str = "UNKNOWN"   # NO_RISK / CLEAN / LOW / MEDIUM / HIGH / CRITICAL

    def to_dict(self) -> dict:
        """Return a plain dict suitable for JSON serialisation / DataFrame creation."""
        return {
            "domain": self.domain,
            "input_url": self.input_url,
            # SEO
            "authority_score": self.authority_score,
            "organic_traffic": self.organic_traffic,
            "referring_domains": self.referring_domains,
            "total_backlinks": self.total_backlinks,
            "seo_error": self.seo_error,
            "backlinks_error": self.backlinks_error,
            "top_countries": self.top_countries,
            "traffic_by_country": self.traffic_by_country,
            # Rankings
            "core_keyword_hits_count": len(self.core_keyword_hits),
            "core_keyword_hits": self.core_keyword_hits,
            "porn_gambling_keyword_hits_count": len(self.porn_gambling_keyword_hits),
            "porn_gambling_keyword_hits": self.porn_gambling_keyword_hits,
            "rankings_error": self.rankings_error,
            # Links
            "homepage_scraped": self.homepage_scraped,
            "total_links_on_page": self.total_links_on_page,
            "bad_links_count": len(self.bad_links_found),
            "bad_links_found": self.bad_links_found,
            "competitor_links_count": len(self.competitor_links_found),
            "competitor_links_found": self.competitor_links_found,
            "keyword_link_flags_count": len(self.keyword_link_flags),
            "keyword_link_flags": self.keyword_link_flags,
            "scrape_error": self.scrape_error,
            # SERP
            "serp_porn_gambling_results_count": len(self.serp_porn_gambling_results),
            "serp_porn_gambling_results": self.serp_porn_gambling_results,
            "serp_porn_gambling_error": self.serp_porn_gambling_error,
            "serp_core_results_count": len(self.serp_core_results),
            "serp_core_results": self.serp_core_results,
            "serp_core_error": self.serp_core_error,
            "early_failed": self.early_failed,
            "early_fail_reason": self.early_fail_reason,
            "outbound_classification": self.outbound_classification,
            "reciprocal_links": self.reciprocal_links,
            "reciprocal_strange_link_count": sum(
                1 for r in self.reciprocal_links if r.get("links_back")
            ),
            "business_legitimacy": self.business_legitimacy,
            # AI analysis
            "risk_level": self.risk_level,
            "ai_analysis_summary": self.ai_analysis.get("summary", ""),
            "ai_analysis_recommendation": self.ai_analysis.get("recommendation", ""),
            "ai_brand_safe": self.ai_analysis.get("brand_safe"),
            "ai_competitor_risk": self.ai_analysis.get("competitor_risk"),
            "ai_key_findings": self.ai_analysis.get("key_findings", []),
            "ai_website_niche": self.ai_analysis.get("website_niche", ""),
            "ai_analysis_error": self.ai_analysis_error,
            # PBN / link-network
            "pbn_risk": self.pbn.get("pbn_risk"),
            "pbn_score": self.pbn.get("pbn_score"),
            "pbn_reasons": self.pbn.get("reasons", []),
            "pbn_reasoning": self.pbn.get("reasoning", ""),
            "pbn_signals": self.pbn.get("signals", {}),
            "pbn_network": self.pbn.get("network", {}),
            "pbn_domain_age": self.pbn.get("domain_age", {}),
            # Deep page checks
            "deep_page_checks_count": len(self.deep_page_checks),
            "deep_page_checks": self.deep_page_checks,
            "deep_page_bad_links_total": sum(len(c.get("bad_links", [])) for c in self.deep_page_checks),
            # Link building recommendation
            "link_recommendation": self.link_recommendation,
            "link_recommendation_error": self.link_recommendation_error,
        }


# ── Domain normalisation ───────────────────────────────────────────────────────

def _normalise_url(raw: str) -> tuple[str, str]:
    """
    Return (full_url, clean_domain) from a user-supplied input.
    Adds https:// if missing so we can scrape the homepage.
    """
    raw = raw.strip()
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        raw = "https://" + raw
    parsed = urlparse(raw)
    domain = parsed.netloc.lower().removeprefix("www.")
    return raw, domain


# ── Single-domain audit ────────────────────────────────────────────────────────

def audit_domain(input_url: str, linkbuilding_targets: Optional[list[dict]] = None) -> AuditResult:
    """
    Run the full audit pipeline for one domain.

    `linkbuilding_targets` is an optional list of {keyword, url} dicts supplied
    by the caller (e.g. the UI text area). If None, targets are loaded from the
    configured file instead.

    Steps:
      1. SEMrush domain overview
      2. SEMrush backlinks overview
      3. SEMrush organic rankings → cross-check against core + porn/gambling keywords
      4. SEMrush AI overview data
      5. Bright Data Web Unlocker → scrape homepage → check links
      6. Bright Data SERP API → site:domain search for porn/gambling
      7. OpenAI analysis of all findings
    """
    full_url, domain = _normalise_url(input_url)
    result = AuditResult(domain=domain, input_url=full_url)

    core_kws = get_core_keywords()[: settings.MAX_KEYWORDS_CHECK]
    porn_kws = get_porn_gambling_keywords()  # no limit – check full list

    # ── GATE (first, serial): scrape homepage, hard-fail on gambling/porn links ─
    logger.info("[%s] Gate: scraping homepage and checking for gambling/porn links…", domain)
    html, scrape_err = bdata.scrape_page(full_url)
    result.scrape_error = scrape_err
    _gate_buckets: dict = {}
    _gate_verdicts: list[dict] = []
    if html:
        failed, offending, reason, _gate_buckets, _gate_verdicts = _homepage_gambling_gate(domain, html)
        if failed:
            result.homepage_scraped = True
            result.bad_links_found = offending
            result.early_failed = True
            result.early_fail_reason = reason
            result.risk_level = "BAD"
            result.ai_analysis = {"summary": reason, "risk_level": "BAD"}
            logger.warning("[%s] HARD FAIL at homepage gate: %s", domain, reason)
            return result

    # ── Wave 1: fire the remaining independent network calls concurrently ──────
    logger.info("[%s] Gate passed. Running parallel data collection…", domain)
    with ThreadPoolExecutor(max_workers=8) as pool:
        f_overview = pool.submit(semrush.get_domain_overview, domain)
        f_backlinks = pool.submit(semrush.get_backlinks_overview, domain)
        f_serp = pool.submit(bdata.site_search_porn_gambling, domain)
        f_serp_core = pool.submit(bdata.site_search_core, domain)
        f_net = pool.submit(pbn_service.network_footprint, domain)
        f_age = pool.submit(pbn_service.domain_age, domain)

        overview = f_overview.result()
        backlinks = f_backlinks.result()
        serp_results, serp_err = f_serp.result()
        serp_core_results, serp_core_err = f_serp_core.result()
        network = f_net.result()
        domain_age_info = f_age.result()

    # ── SEO metrics ───────────────────────────────────────────────────────────
    result.authority_score = overview.authority_score
    result.organic_traffic = overview.organic_traffic
    result.seo_error = overview.error
    _total_keywords = overview.organic_keywords  # for proportional PBN scoring

    result.referring_domains = backlinks.referring_domains
    result.total_backlinks = backlinks.total_backlinks
    result.backlinks_error = backlinks.error
    # Authority Score comes from backlinks_overview (not domain_ranks)
    if backlinks.authority_score is not None:
        result.authority_score = backlinks.authority_score

    # ── Wave 2: SEMrush rank-POSITION checks (core + danger keywords) ──────────
    # No giant rankings pull. Instead one targeted query per keyword, which
    # returns the keyword AND its position (e.g. "residential proxy" -> pos 4),
    # across the domain's top traffic markets. ~10 units per keyword vs ~10,000
    # for the old 1,000-row pull.
    _target_dbs = getattr(overview, "top_databases", None) or ["us"]
    result.top_countries = getattr(overview, "top_databases", []) or []
    result.traffic_by_country = getattr(overview, "traffic_by_country", {}) or {}
    logger.info("[%s] SEMrush keyword/position checks target markets: %s", domain, ", ".join(_target_dbs))
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_bad = pool.submit(semrush.get_organic_keywords_for_terms, domain, porn_kws, 50, _target_dbs)
        f_core = pool.submit(semrush.get_organic_keywords_for_terms, domain, core_kws, 50, _target_dbs)
        bad_kws = f_bad.result()
        core_hits = f_core.result()

    # ── Core business keywords → hits with positions ──────────────────────────
    result.core_keyword_hits = [
        {"keyword": k.phrase, "position": k.position, "url": k.url or ""}
        for k in core_hits
    ]

    # ── Danger keywords → hits with positions (word-boundary filtered) ────────
    # SEMrush's "contains" filter is substring-based and yields false positives
    # (e.g. "sex" inside "Sussex"); keep only whole-word matches.
    _pg_patterns = _danger_patterns(porn_kws)
    _seen_pg: set[str] = set()
    for k in bad_kws:
        phrase = (k.phrase or "").lower()
        if not phrase or phrase in _seen_pg:
            continue
        if not any(p.search(phrase) for p in _pg_patterns):
            continue
        _seen_pg.add(phrase)
        result.porn_gambling_keyword_hits.append(
            {"keyword": phrase, "position": k.position, "url": k.url or ""}
        )

    # ── Homepage: link checks + page text (niche context) + outbound profile ──
    _outbound_profile = {"distinct_external_domains": 0, "external_domains": []}
    if html:
        result.homepage_scraped = True
        result.homepage_text = link_checker.extract_page_text(html)
        link_result = link_checker.check_links(html, full_url)
        result.total_links_on_page = link_result.total_links_found
        result.bad_links_found = [
            {
                "found_href": m.found_href,
                "matched_bad_domain": m.matched_bad_domain,
                "link_text": m.link_text,
            }
            for m in link_result.bad_link_matches
        ]
        result.competitor_links_found = link_checker.check_competitor_links(html, full_url)
        # Secondary: keyword signals in external links only
        result.keyword_link_flags = link_checker.keyword_links_present(html, porn_kws[:30], source_domain=domain)
        # Outbound-domain profile for PBN scoring
        _outbound_profile = link_checker.external_domain_profile(html, full_url)

        # ── About page: find the real "About" link from the homepage, scrape it ──
        about_url = link_checker.find_about_url(html, full_url)
        if about_url:
            _about_html, _about_err = bdata.scrape_page(about_url)
            if _about_html and not _about_err:
                _about_text = link_checker.extract_page_text(_about_html)
                if len(_about_text) > 100:
                    result.about_page_text = _about_text
                    logger.info("[%s] About page scraped from %s (%d chars)", domain, about_url, len(_about_text))
        if not result.about_page_text:
            logger.info("[%s] No about page found — relying on homepage text for niche.", domain)

    # ── Outbound classification → reciprocity → legitimacy (PBN link scheme) ──
    from services import legitimacy_service as legit

    # Business legitimacy of the audited site (homepage + about text).
    result.business_legitimacy = legit.assess(
        html or "", (result.homepage_text or "") + "\n" + (result.about_page_text or "")
    )

    _strange: list[str] = []
    if html:
        _strange = [v["domain"] for v in _gate_verdicts if v.get("category") == "strange"]
        result.outbound_classification = {
            "own_entity": _gate_buckets.get("own_entity", []),
            "legit": _gate_buckets.get("legit", []) + [v["domain"] for v in _gate_verdicts if v.get("category") == "legit"],
            "strange": _strange,
        }

    # Reciprocity: do the strange partners link back to us?
    if _strange and settings.ENABLE_RECIPROCITY and settings.RECIPROCAL_MAX_CHECKS > 0:
        targets = _strange[: settings.RECIPROCAL_MAX_CHECKS]
        logger.info("[%s] Reciprocity check on %d strange domain(s)…", domain, len(targets))

        def _check_partner(partner: str) -> dict:
            entry = {"partner": partner, "links_back": False, "partner_legit": None}
            try:
                p_html, p_err = bdata.scrape_page(f"https://{partner}")
                if p_err or not p_html:
                    return entry
                entry["links_back"] = link_checker.links_back(p_html, f"https://{partner}", domain)
                if entry["links_back"]:
                    # Only deep-check legitimacy of partners that actually link back.
                    p_text = link_checker.extract_page_text(p_html)
                    about = link_checker.find_about_url(p_html, f"https://{partner}")
                    if about:
                        a_html, a_err = bdata.scrape_page(about)
                        if a_html and not a_err:
                            p_text += "\n" + link_checker.extract_page_text(a_html)
                    entry["partner_legit"] = legit.assess(p_html, p_text)["is_legit"]
            except Exception as exc:
                logger.warning("[%s] reciprocity check failed for %s: %s", domain, partner, exc)
            return entry

        with ThreadPoolExecutor(max_workers=min(settings.INNER_CONCURRENCY, len(targets))) as pool:
            result.reciprocal_links = list(pool.map(_check_partner, targets))

    # ── SERP results (Bright Data Google site: checks) ────────────────────────
    result.serp_porn_gambling_results = serp_results
    result.serp_porn_gambling_error = serp_err
    result.serp_core_results = serp_core_results
    result.serp_core_error = serp_core_err

    # ── 6b: Deep page link check ──────────────────────────────────────────────
    # Sources:
    #  (a) first 10 unique pages that rank for porn/gambling keywords (SEMrush)
    #  (b) first 5 results per SERP term (already fetched that way)
    # Both sources are merged with deduplication, no hard total cap.
    _semrush_pages = list(dict.fromkeys(
        hit["url"] for hit in result.porn_gambling_keyword_hits
        if hit.get("url") and hit["url"].startswith("http")
    ))[:10]

    # Keep up to 5 per SERP term (they arrive grouped by matched_term already)
    _serp_per_term: dict[str, list[str]] = {}
    for r in result.serp_porn_gambling_results:
        url = r.get("url", "")
        term = r.get("matched_term", "")
        if url and url.startswith("http"):
            _serp_per_term.setdefault(term, [])
            if len(_serp_per_term[term]) < 5:
                _serp_per_term[term].append(url)
    _serp_pages = [url for urls in _serp_per_term.values() for url in urls]

    # Merge: SEMrush pages first, then SERP pages — deduplicate, preserve order
    pg_ranking_urls = list(dict.fromkeys(_semrush_pages + _serp_pages))

    # Cap to bound runtime on spam-heavy domains (each page is a scrape + AI call).
    if len(pg_ranking_urls) > settings.MAX_DEEP_PAGES_PER_DOMAIN:
        logger.info(
            "[%s] Capping deep-check pages %d → %d (MAX_DEEP_PAGES_PER_DOMAIN).",
            domain, len(pg_ranking_urls), settings.MAX_DEEP_PAGES_PER_DOMAIN,
        )
        pg_ranking_urls = pg_ranking_urls[: settings.MAX_DEEP_PAGES_PER_DOMAIN]

    def _deep_check_page(page_url: str) -> dict:
        # Work out what flagged this page (SEMrush keyword or SERP term)
        _trigger = next(
            (h["keyword"] for h in result.porn_gambling_keyword_hits if h.get("url") == page_url),
            next(
                (r.get("matched_term", "") for r in result.serp_porn_gambling_results if r.get("url") == page_url),
                "serp discovery",
            ),
        )
        check_entry: dict = {
            "page_url": page_url,
            "triggering_keyword": _trigger,
            "total_links": 0,
            "bad_links": [],
            "keyword_flags": [],
            "competitor_links": [],
            "ai_flagged_links": [],
            "error": None,
        }
        try:
            page_html, page_err = bdata.scrape_page(page_url)
            if page_err:
                check_entry["error"] = page_err
            elif page_html:
                page_link_result = link_checker.check_links(page_html, page_url)
                check_entry["total_links"] = page_link_result.total_links_found
                check_entry["bad_links"] = [
                    {
                        "found_href": m.found_href,
                        "matched_bad_domain": m.matched_bad_domain,
                        "link_text": m.link_text,
                    }
                    for m in page_link_result.bad_link_matches
                ]
                # External-only keyword flags (skip internal links)
                check_entry["keyword_flags"] = link_checker.keyword_links_present(
                    page_html, porn_kws[:30], source_domain=domain
                )
                # Competitor link check on this deep page
                check_entry["competitor_links"] = link_checker.check_competitor_links(page_html, page_url)

                # AI-powered outbound link classification:
                # Extract body-only external links (excluding nav/footer) and ask the
                # LLM to classify any gambling/adult destinations — this catches sites
                # like splashcoins.com even when not in the known-bad list.
                body_external_links = link_checker.extract_body_external_links(page_html, page_url)
                if body_external_links:
                    logger.info(
                        "[%s] AI-classifying %d body external link(s) on %s…",
                        domain, len(body_external_links), page_url,
                    )
                    ai_flagged = ai_service.classify_outbound_links(page_url, body_external_links)
                    check_entry["ai_flagged_links"] = ai_flagged
                    # Merge AI-flagged links into bad_links so they surface in the UI
                    existing_hrefs = {b["found_href"] for b in check_entry["bad_links"]}
                    for flagged in ai_flagged:
                        href = flagged.get("found_href", "")
                        if href and href not in existing_hrefs:
                            check_entry["bad_links"].append({
                                "found_href": href,
                                "matched_bad_domain": f"[AI: {flagged.get('category', 'harmful')}]",
                                "link_text": flagged.get("reason", ""),
                            })
                            existing_hrefs.add(href)
        except Exception as exc:
            check_entry["error"] = str(exc)
        return check_entry

    if pg_ranking_urls:
        logger.info("[%s] Deep-checking %d porn/gambling ranking page(s)…", domain, len(pg_ranking_urls))
        # Pages are independent — scrape + classify them concurrently (gently).
        with ThreadPoolExecutor(max_workers=min(settings.INNER_CONCURRENCY, len(pg_ranking_urls))) as pool:
            entries = list(pool.map(_deep_check_page, pg_ranking_urls))
        for entry in entries:
            result.competitor_links_found.extend(entry.get("competitor_links", []))
            result.deep_page_checks.append(entry)
    else:
        logger.info("[%s] No porn/gambling ranking pages to deep-check.", domain)

    # ── 7: AI analysis + PBN verdict (the two LLM calls run in parallel) ───────
    _pbn_signals = pbn_service.compute_signals(
        referring_domains=result.referring_domains,
        total_backlinks=result.total_backlinks,
        organic_traffic=result.organic_traffic,
        authority_score=result.authority_score,
        pg_keyword_hit_count=len(result.porn_gambling_keyword_hits),
        homepage_text=result.homepage_text or "",
        distinct_external_domains=_outbound_profile.get("distinct_external_domains", 0),
        network=network,
        age=domain_age_info,
        total_ranked_keywords=_total_keywords,
        reciprocal_links=result.reciprocal_links,
        business_legitimacy=result.business_legitimacy,
    )
    _ranking_sample = (
        [h["keyword"] for h in result.porn_gambling_keyword_hits[:30]]
        + [h["keyword"] for h in result.core_keyword_hits[:10]]
    )

    logger.info("[%s] Running AI analysis + PBN verdict…", domain)
    _audit_dict = result.to_dict()
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_analysis = pool.submit(
            ai_service.analyze_audit, _audit_dict,
            result.about_page_text or "", result.homepage_text or "",
        )
        f_pbn = pool.submit(
            ai_service.assess_pbn, _pbn_signals["signals"], _pbn_signals["reasons"],
            result.homepage_text or "", _ranking_sample,
        )
        analysis = f_analysis.result()
        _pbn_verdict = f_pbn.result()

    result.ai_analysis = analysis
    result.ai_analysis_error = analysis.get("error")
    result.risk_level = analysis.get("risk_level", "UNKNOWN")

    # Assemble PBN result: LLM verdict + heuristic prior + raw signals.
    _pbn_risk = _pbn_verdict.get("pbn_risk") or _pbn_signals["pbn_heuristic_band"]
    if _pbn_signals["pbn_score"] >= 45 and _pbn_risk == "LOW":
        _pbn_risk = "MEDIUM"  # heuristic floor when signals are strong
    # Reconcile the numeric score with the final verdict so the UI never shows a
    # contradiction like "HIGH (score 8/100)". The score reflects the verdict,
    # lifted further by the rule-based signal strength.
    _band_floor = {"LOW": 10, "MEDIUM": 45, "HIGH": 75}.get(_pbn_risk, 0)
    _display_score = max(_pbn_signals["pbn_score"], _band_floor)
    result.pbn = {
        "pbn_risk": _pbn_risk,
        "pbn_score": _display_score,
        "rule_score": _pbn_signals["pbn_score"],
        "heuristic_band": _pbn_signals["pbn_heuristic_band"],
        "reasons": _pbn_signals["reasons"],
        "reasoning": _pbn_verdict.get("reasoning", ""),
        "signals": _pbn_signals["signals"],
        "network": network,
        "domain_age": domain_age_info,
        "error": _pbn_verdict.get("error"),
    }
    # ── 7b: Rule-based risk overrides ─────────────────────────────────────────────
    #
    # Rule 1 – CRITICAL (immediate): homepage links to bad/gambling/adult sites.
    _homepage_bad_link_count = len(result.bad_links_found)
    if _homepage_bad_link_count > 0:
        result.risk_level = "CRITICAL"
        logger.info(
            "[%s] OVERRIDE → CRITICAL: homepage has %d bad outbound link(s).",
            domain, _homepage_bad_link_count,
        )

    # Remaining rules only apply when homepage was not CRITICAL.
    elif result.risk_level != "CRITICAL":
        _has_pg_keywords = bool(result.porn_gambling_keyword_hits)

        # "Confirmed" SERP result: the matched keyword appears in the URL path,
        # meaning the page is plausibly about that topic. Google site: searches
        # often return unrelated pages (e.g. site:x.com casino -> /recipes/);
        # those are treated as noise and excluded from risk signals.
        _confirmed_serp = [
            r for r in result.serp_porn_gambling_results
            if r.get("matched_term", "").lower() in (r.get("url", "") or "").lower()
        ]
        _has_confirmed_serp = bool(_confirmed_serp)

        _total_bad_links = len(result.bad_links_found) + sum(
            len(c.get("bad_links", [])) for c in result.deep_page_checks
        )

        # Rule 2 – NO_RISK: no P/G keyword hits, no confirmed SERP hits, no bad links.
        if not _has_pg_keywords and not _has_confirmed_serp and _total_bad_links == 0:
            result.risk_level = "NO_RISK"
            logger.info(
                "[%s] OVERRIDE → NO_RISK: no genuine P/G signals and no bad links.", domain
            )

        # Rule 3 – LOW: has signals but deep checks found no pages with bad links.
        # Applies regardless of OpenAI score (AI can over-flag on noisy signals).
        elif _has_pg_keywords or _has_confirmed_serp:
            _deep_pages_with_bad_links = sum(
                1 for c in result.deep_page_checks if c.get("bad_links")
            )
            if _deep_pages_with_bad_links == 0:
                result.risk_level = "LOW"
                logger.info(
                    "[%s] OVERRIDE → LOW: P/G signals present but zero pages with bad links.",
                    domain,
                )
            elif _deep_pages_with_bad_links < 3:
                # 1-2 offending pages and fewer than 5 total bad links -- cap at LOW
                # If there are many bad links even on one page, trust the higher rating.
                _total_bad_link_count = sum(
                    len(c.get("bad_links", [])) for c in result.deep_page_checks
                )
                if result.risk_level in ("HIGH", "MEDIUM") and _total_bad_link_count < 5:
                    result.risk_level = "LOW"
                    logger.info(
                        "[%s] OVERRIDE → LOW: only %d page(s) and %d total bad links.",
                        domain, _deep_pages_with_bad_links, _total_bad_link_count,
                    )

    # ── 8: Link building recommendation ──────────────────────────────────────
    lb_targets = linkbuilding_targets if linkbuilding_targets is not None else get_linkbuilding_targets()
    if lb_targets:
        logger.info("[%s] Generating link building recommendation…", domain)
        link_rec = ai_service.recommend_link_building(
            result.to_dict(), lb_targets, settings.LINKBUILDING_TARGET_DOMAIN,
            about_page_text=result.about_page_text or "",
            homepage_text=result.homepage_text or "",
        )
        result.link_recommendation = link_rec
        result.link_recommendation_error = link_rec.get("error")
    else:
        logger.info("[%s] No link-building targets configured – skipping recommendation.", domain)

    logger.info("[%s] Audit complete – risk level: %s", domain, result.risk_level)
    return result


# ── Cross-batch footprint (PBN cluster detection) ───────────────────────────────

# Managed-DNS / CDN providers whose IPs are shared by millions of unrelated
# sites — co-location on these tells you nothing, so we skip them.
_COMMON_DNS_PROVIDERS = (
    "cloudflare", "google", "awsdns", "amazonaws", "azure-dns", "godaddy",
    "domaincontrol", "namecheap", "registrar-servers", "digitalocean", "nsone",
    "akam", "wixdns", "squarespace", "dnsimple", "name-services",
)


def _is_common_dns_provider(nameservers: list[str]) -> bool:
    return any(any(p in ns for p in _COMMON_DNS_PROVIDERS) for ns in nameservers)


def _apply_cross_batch_footprint(results: list[AuditResult]) -> None:
    """
    Flag domains in the batch that are hosted on the same IP — a strong PBN
    cluster tell. CDN/managed-DNS-fronted domains are skipped (their shared IPs
    are meaningless), so this only clusters genuinely co-hosted sites.
    """
    groups: dict[str, list[AuditResult]] = {}
    for r in results:
        if not r or not getattr(r, "pbn", None):
            continue
        net = r.pbn.get("network") or {}
        ip = net.get("ip")
        ns = net.get("nameservers") or []
        if not ip or _is_common_dns_provider(ns):
            continue
        groups.setdefault(ip, []).append(r)

    for ip, group in groups.items():
        if len(group) < 2:
            continue
        domains = [g.domain for g in group]
        for r in group:
            peers = [d for d in domains if d != r.domain]
            r.pbn.setdefault("reasons", []).append(
                f"Hosted on the same IP ({ip}) as {len(peers)} other audited domain(s): "
                f"{', '.join(peers[:5])} — possible link-network cluster (verify)."
            )
            r.pbn["shared_hosting_with"] = peers
            if r.pbn.get("pbn_risk") == "LOW":
                r.pbn["pbn_risk"] = "MEDIUM"


# ── Bulk audit ─────────────────────────────────────────────────────────────────

def audit_bulk(
    urls: list[str],
    progress_callback: Optional[Callable[[str, int, int], None]] = None,
    linkbuilding_targets: Optional[list[dict]] = None,
) -> list[AuditResult]:
    """
    Audit multiple domains concurrently.

    progress_callback(domain, completed_count, total_count) is called after
    each domain finishes so the UI can update a progress bar.

    `linkbuilding_targets` is an optional list of {keyword, url} dicts forwarded
    to each domain audit. If None, each audit falls back to the targets file.
    """
    total = len(urls)
    results: list[AuditResult] = [None] * total  # preserve order
    url_index = {url: i for i, url in enumerate(urls)}
    completed = 0

    max_workers = min(settings.MAX_CONCURRENT_AUDITS, total)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {
            executor.submit(audit_domain, url, linkbuilding_targets): url for url in urls
        }
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            idx = url_index[url]
            try:
                results[idx] = future.result()
            except Exception as exc:
                logger.exception("Unhandled error auditing %s", url)
                _, domain = _normalise_url(url)
                results[idx] = AuditResult(
                    domain=domain,
                    input_url=url,
                    risk_level="ERROR",
                    ai_analysis_error=str(exc),
                )
            completed += 1
            if progress_callback:
                try:
                    progress_callback(url, completed, total)
                except Exception:
                    pass

    # Cross-domain PBN footprint: flag co-hosted clusters within this batch.
    _apply_cross_batch_footprint(results)

    return results

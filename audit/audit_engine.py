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
from services import semrush_service as semrush
from services import bright_data_service as bdata
from services import link_checker_service as link_checker
from services import openai_service as ai_service

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
        _core_keywords = _load_keywords(settings.CORE_KEYWORDS_FILE)
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
    """Force reload of all keyword lists and link-building targets."""
    global _core_keywords, _porn_gambling_keywords, _linkbuilding_targets
    _core_keywords = []
    _porn_gambling_keywords = []
    _linkbuilding_targets = []


# ── Keyword matching helpers ───────────────────────────────────────────────────

def _keywords_found_in_rankings(
    ranked_keywords: list[semrush.OrganicKeyword],
    target_keywords: list[str],
) -> list[dict]:
    """
    Return list of {keyword, position, url} where a danger term appears as a
    substring inside the domain's ranked phrase (one-directional check only).

    One-directional (`t in phrase`) avoids false positives from the reverse
    check (`phrase in t`), which would flag short ranked phrases like "bet" or
    "in" because they happen to be substrings of danger terms.
    """
    hits = []
    ranked_lower = [
        (kw.phrase.lower(), kw.position, kw.url or "") for kw in ranked_keywords
    ]
    for target in target_keywords:
        t = target.lower()
        for phrase, pos, url in ranked_lower:
            if t in phrase:  # danger term is a substring of the ranked phrase
                hits.append({"keyword": phrase, "position": pos, "url": url})
    # Deduplicate by keyword
    seen = set()
    unique = []
    for h in hits:
        if h["keyword"] not in seen:
            seen.add(h["keyword"])
            unique.append(h)
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

    # ── AI Analysis (OpenAI) ──────────────────────────────────────────────────
    ai_analysis: dict = field(default_factory=dict)
    ai_analysis_error: Optional[str] = None

    # ── Link Building Recommendation (OpenAI) ────────────────────────────────
    link_recommendation: dict = field(default_factory=dict)
    link_recommendation_error: Optional[str] = None

    # ── About page ─────────────────────────────────────────────────────────────
    about_page_text: Optional[str] = None   # extracted visible text from /about (or similar)

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
            # AI analysis
            "risk_level": self.risk_level,
            "ai_analysis_summary": self.ai_analysis.get("summary", ""),
            "ai_analysis_recommendation": self.ai_analysis.get("recommendation", ""),
            "ai_brand_safe": self.ai_analysis.get("brand_safe"),
            "ai_competitor_risk": self.ai_analysis.get("competitor_risk"),
            "ai_key_findings": self.ai_analysis.get("key_findings", []),
            "ai_analysis_error": self.ai_analysis_error,
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
    domain = parsed.netloc.lower().lstrip("www.")
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

    # ── 1 & 2: SEMrush SEO metrics ────────────────────────────────────────────
    logger.info("[%s] Fetching SEMrush domain overview…", domain)
    overview = semrush.get_domain_overview(domain)
    result.authority_score = overview.authority_score
    result.organic_traffic = overview.organic_traffic
    result.seo_error = overview.error

    logger.info("[%s] Fetching SEMrush backlinks overview…", domain)
    backlinks = semrush.get_backlinks_overview(domain)
    result.referring_domains = backlinks.referring_domains
    result.total_backlinks = backlinks.total_backlinks
    result.backlinks_error = backlinks.error
    # Authority Score comes from backlinks_overview (not domain_ranks)
    if backlinks.authority_score is not None:
        result.authority_score = backlinks.authority_score

    # ── 3: Organic rankings → keyword cross-check ─────────────────────────────
    logger.info("[%s] Fetching SEMrush organic rankings (top-1000 by position)…", domain)
    rankings = semrush.get_organic_rankings(domain, limit=1000)
    result.rankings_error = rankings.error
    if rankings.keywords:
        result.core_keyword_hits = _keywords_found_in_rankings(rankings.keywords, core_kws)
        result.porn_gambling_keyword_hits = _keywords_found_in_rankings(rankings.keywords, porn_kws)

    # ── 3b: Targeted SEMrush filtered queries per danger term ─────────────────
    # Large domains rank for thousands of keywords. The top-1000 scan misses
    # danger terms ranked below ~position 50. One display_filter query per
    # porn/gambling term directly asks SEMrush: "does this domain rank for ANY
    # keyword containing 'casino'?" — regardless of overall ranking count.
    logger.info("[%s] Running targeted SEMrush porn/gambling filtered queries…", domain)
    targeted_kws = semrush.get_organic_keywords_for_terms(domain, porn_kws)
    if targeted_kws:
        # Build an index of existing hits so we can update empty URLs in-place
        existing_index: dict[str, int] = {
            h["keyword"].lower(): i
            for i, h in enumerate(result.porn_gambling_keyword_hits)
        }
        for kw in targeted_kws:
            phrase = kw.phrase.lower()
            url = kw.url or ""
            if phrase in existing_index:
                # Update URL if the existing entry had none
                idx = existing_index[phrase]
                if not result.porn_gambling_keyword_hits[idx].get("url") and url:
                    result.porn_gambling_keyword_hits[idx]["url"] = url
            else:
                existing_index[phrase] = len(result.porn_gambling_keyword_hits)
                result.porn_gambling_keyword_hits.append({
                    "keyword": phrase,
                    "position": kw.position,
                    "url": url,
                })

    # ── 4 (removed): SEMrush AI overview was here – dropped (UI-only data)

    # ── 5: Scrape homepage & check links ──────────────────────────────────────
    logger.info("[%s] Scraping homepage via Bright Data Web Unlocker…", domain)
    html, scrape_err = bdata.scrape_page(full_url)
    result.scrape_error = scrape_err
    if html:
        result.homepage_scraped = True
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
    # ── 5b: Scrape about page to understand site niche ─────────────────────────────
    _about_candidates = [
        f"https://{domain}/about",
        f"https://{domain}/about-us",
        f"https://{domain}/about_us",
        f"https://www.{domain}/about",
        f"https://www.{domain}/about-us",
    ]
    for _about_url in _about_candidates:
        try:
            _about_html, _about_err = bdata.scrape_page(_about_url)
            if _about_html and not _about_err:
                _about_soup = BeautifulSoup(_about_html, "lxml")
                # Remove nav/footer/header noise, keep body text
                for _tag in _about_soup(["script", "style", "nav", "footer", "header"]):
                    _tag.decompose()
                _about_text = " ".join(_about_soup.get_text(separator=" ").split())
                if len(_about_text) > 100:  # ignore trivially short responses
                    result.about_page_text = _about_text[:3000]  # cap at 3k chars
                    logger.info("[%s] About page scraped from %s (%d chars)", domain, _about_url, len(result.about_page_text))
                    break
        except Exception as _exc:
            logger.debug("[%s] About page attempt failed for %s: %s", domain, _about_url, _exc)
    if not result.about_page_text:
        logger.info("[%s] No about page found — will rely on homepage for niche context.", domain)
    # ── 6: SERP porn/gambling site: search ────────────────────────────────────
    logger.info("[%s] Running SERP porn/gambling check via Bright Data…", domain)
    serp_results, serp_err = bdata.site_search_porn_gambling(domain)
    result.serp_porn_gambling_results = serp_results
    result.serp_porn_gambling_error = serp_err

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

    if pg_ranking_urls:
        logger.info("[%s] Deep-checking %d porn/gambling ranking page(s)…", domain, len(pg_ranking_urls))
        for page_url in pg_ranking_urls:
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
                    page_comp_links = link_checker.check_competitor_links(page_html, page_url)
                    check_entry["competitor_links"] = page_comp_links
                    result.competitor_links_found.extend(page_comp_links)

                    # AI-powered outbound link classification:
                    # Extract body-only external links (excluding nav/footer) and
                    # ask OpenAI to classify any gambling/adult destinations — this
                    # catches sites like splashcoins.com even when not in the known-bad list.
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
                    else:
                        check_entry["ai_flagged_links"] = []
            except Exception as exc:
                check_entry["error"] = str(exc)
            result.deep_page_checks.append(check_entry)
    else:
        logger.info("[%s] No porn/gambling ranking pages to deep-check.", domain)

    # ── 7: OpenAI analysis ────────────────────────────────────────────────────
    logger.info("[%s] Running OpenAI analysis…", domain)
    analysis = ai_service.analyze_audit(result.to_dict(), about_page_text=result.about_page_text or "")
    result.ai_analysis = analysis
    result.ai_analysis_error = analysis.get("error")
    result.risk_level = analysis.get("risk_level", "UNKNOWN")
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
        )
        result.link_recommendation = link_rec
        result.link_recommendation_error = link_rec.get("error")
    else:
        logger.info("[%s] No link-building targets configured – skipping recommendation.", domain)

    logger.info("[%s] Audit complete – risk level: %s", domain, result.risk_level)
    return result


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

    return results

"""
Link checker service.

Given the raw HTML of a page this module:
  1. Extracts all outbound links (href attributes from <a> tags).
  2. Checks those links against the known-bad-sites list.
  3. Reports any matches with the matched bad domain and the full href found.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from config import settings

logger = logging.getLogger(__name__)


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class BadLinkMatch:
    found_href: str          # The full href as it appeared in the HTML
    matched_bad_domain: str  # The entry from the bad-sites list that matched
    link_text: str = ""      # Anchor text (if any)
    context: str = ""        # Short surrounding HTML context


@dataclass
class LinkCheckResult:
    source_url: str
    total_links_found: int = 0
    external_links: list[str] = field(default_factory=list)
    bad_link_matches: list[BadLinkMatch] = field(default_factory=list)
    error: Optional[str] = None


# ── Bad-sites list loader ──────────────────────────────────────────────────────

_BAD_DOMAINS: list[str] = []


def _load_bad_domains() -> list[str]:
    """Load known bad domains from the data file (cached after first load)."""
    global _BAD_DOMAINS
    if _BAD_DOMAINS:
        return _BAD_DOMAINS
    path: Path = settings.KNOWN_BAD_SITES_FILE
    if not path.exists():
        logger.warning("Known bad sites file not found: %s", path)
        return []
    domains = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().lower()
        if line and not line.startswith("#"):
            # Normalise: strip protocol if someone accidentally added it
            for prefix in ("https://", "http://"):
                if line.startswith(prefix):
                    line = line[len(prefix):]
            domains.append(line.rstrip("/"))
    _BAD_DOMAINS = domains
    return domains


def reload_bad_domains() -> None:
    """Force reload of the bad-domains list (call after editing the file)."""
    global _BAD_DOMAINS
    _BAD_DOMAINS = []
    _load_bad_domains()


# ── Competitor-sites list loader ───────────────────────────────────────────────

_COMPETITOR_DOMAINS: list[str] = []


def _load_competitor_domains() -> list[str]:
    """Load competitor domains from the data file (cached after first load)."""
    global _COMPETITOR_DOMAINS
    if _COMPETITOR_DOMAINS:
        return _COMPETITOR_DOMAINS
    path: Path = settings.COMPETITOR_SITES_FILE
    if not path.exists():
        logger.warning("Competitor sites file not found: %s", path)
        return []
    domains = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().lower()
        if line and not line.startswith("#"):
            for prefix in ("https://", "http://"):
                if line.startswith(prefix):
                    line = line[len(prefix):]
            domains.append(line.rstrip("/"))
    _COMPETITOR_DOMAINS = domains
    return domains


def reload_competitor_domains() -> None:
    """Force reload of the competitor-domains list."""
    global _COMPETITOR_DOMAINS
    _COMPETITOR_DOMAINS = []
    _load_competitor_domains()


def check_competitor_links(html: str, source_url: str) -> list[dict]:
    """
    Parse *html* and return any outbound links pointing to competitor domains.
    Each result dict: {found_href, matched_competitor, link_text}
    """
    competitor_domains = _load_competitor_domains()
    if not competitor_domains:
        return []

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return []

    source_domain = _extract_domain(source_url)
    matches = []

    for tag in soup.find_all("a", href=True):
        href: str = tag["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        href_domain = _extract_domain(href)
        if not href_domain:
            continue
        if source_domain and href_domain.endswith(source_domain):
            continue
        for comp_domain in competitor_domains:
            if _is_match(href_domain, comp_domain):
                matches.append({
                    "found_href": href,
                    "matched_competitor": comp_domain,
                    "link_text": tag.get_text(strip=True)[:120],
                })
                break

    return matches


# ── Core logic ────────────────────────────────────────────────────────────────

def _extract_domain(href: str) -> str:
    """Return lowercase netloc from an href, or '' if unparseable."""
    try:
        parsed = urlparse(href)
        return parsed.netloc.lower().lstrip("www.")
    except Exception:
        return ""


def _is_match(href_domain: str, bad_domain: str) -> bool:
    """
    Return True if href_domain is or is a subdomain of bad_domain.
    Examples:
      "royalcasino.dk"        vs "royalcasino.dk"       → True
      "sub.royalcasino.dk"    vs "royalcasino.dk"       → True
      "notaroyalcasino.dk"    vs "royalcasino.dk"       → False
    """
    return href_domain == bad_domain or href_domain.endswith("." + bad_domain)


def check_links(html: str, source_url: str) -> LinkCheckResult:
    """
    Parse *html* and check every anchor href against the bad-domains list.

    Returns a LinkCheckResult with all bad-link matches found.
    """
    result = LinkCheckResult(source_url=source_url)
    bad_domains = _load_bad_domains()

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as exc:
        result.error = f"HTML parse error: {exc}"
        return result

    anchors = soup.find_all("a", href=True)
    result.total_links_found = len(anchors)

    source_domain = _extract_domain(source_url)

    for tag in anchors:
        href: str = tag["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue

        href_domain = _extract_domain(href)
        if not href_domain:
            continue

        # Only consider external links (not the site's own domain)
        if source_domain and href_domain.endswith(source_domain):
            continue

        result.external_links.append(href)

        # Check against every known bad domain
        for bad_domain in bad_domains:
            if _is_match(href_domain, bad_domain):
                link_text = tag.get_text(strip=True)[:120]
                context = str(tag)[:300]
                result.bad_link_matches.append(
                    BadLinkMatch(
                        found_href=href,
                        matched_bad_domain=bad_domain,
                        link_text=link_text,
                        context=context,
                    )
                )
                break  # one match per href is enough

    return result


def keyword_links_present(html: str, keywords: list[str], source_domain: str = "") -> list[str]:
    """
    Scan anchor texts and hrefs for gambling/adult keyword signals.

    When *source_domain* is provided, only external links are checked
    (links pointing outside that domain are flagged; internal links are skipped).

    Returns a list of strings describing each flagged link.
    """
    if not html or not keywords:
        return []

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return []

    flags: list[str] = []
    kw_patterns = [(kw, re.compile(re.escape(kw), re.IGNORECASE)) for kw in keywords]
    norm_source = source_domain.lower().lstrip("www.")

    for tag in soup.find_all("a", href=True):
        href: str = tag["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue

        # Skip internal links when source_domain is given
        if norm_source:
            href_domain = _extract_domain(href)
            if not href_domain or href_domain.endswith(norm_source):
                continue

        text: str = tag.get_text(strip=True)
        combined = f"{href} {text}"

        for kw, pattern in kw_patterns:
            if pattern.search(combined):
                flags.append(f'"{kw}" found in link: {href[:120]}')
                break  # one flag per link

    return flags

# ── Body-only external link extractor ─────────────────────────────────────────

# Tags that are typically navigation or footer elements to exclude
_NAV_FOOTER_TAGS = {"nav", "header", "footer"}
_NAV_FOOTER_CLASSES = {
    "nav", "navbar", "navigation", "menu", "header", "site-header",
    "footer", "site-footer", "footer-links", "footer-nav",
}


def _is_nav_or_footer(tag) -> bool:
    """Return True if *tag* lives inside a nav, header, or footer element."""
    for parent in tag.parents:
        if parent.name in _NAV_FOOTER_TAGS:
            return True
        parent_classes = set(parent.get("class") or [])
        if parent_classes & _NAV_FOOTER_CLASSES:
            return True
        if parent.get("id", "").lower() in _NAV_FOOTER_CLASSES:
            return True
    return False


def extract_body_external_links(html: str, source_url: str) -> list[str]:
    """
    Extract all unique external links from the main body of *html*,
    excluding links that appear inside <nav>, <header>, or <footer> elements
    (and elements with common nav/footer CSS classes/ids).

    Returns a deduplicated list of href strings.
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return []

    source_domain = _extract_domain(source_url)
    seen: set[str] = set()
    links: list[str] = []

    for tag in soup.find_all("a", href=True):
        href: str = tag["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        href_domain = _extract_domain(href)
        if not href_domain:
            continue
        # Skip internal links
        if source_domain and href_domain.endswith(source_domain):
            continue
        # Skip nav/footer links
        if _is_nav_or_footer(tag):
            continue
        if href not in seen:
            seen.add(href)
            links.append(href)

    return links
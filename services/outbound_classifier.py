"""
Classify a page's outbound external domains into:
  - own_entity : the audited site's own subdomains + declared hreflang variants
  - legit      : domains on the maintained allowlist (data/legit_domains.txt)
  - candidates : everything left over → passed to the AI to split legit/strange

own_entity and legit links are IGNORED by PBN scoring (no points up or down).
Only the AI-confirmed 'strange' subset of candidates feeds reciprocity + scoring.
"""
from __future__ import annotations

import logging
from typing import Optional

from bs4 import BeautifulSoup

from config import settings
from services.link_checker_service import (
    _extract_domain,
    extract_all_external_links,
    extract_hreflang_alternates,
)

logger = logging.getLogger(__name__)


def _extract_all_links_no_filter(html: str) -> list[str]:
    """
    Extract every outbound href from *html* without filtering subdomains.
    Unlike extract_all_external_links, this does NOT exclude subdomains of the
    source domain — classify_outbound needs to see them so it can bucket
    them as own_entity.
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return []
    seen: set[str] = set()
    links: list[str] = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        href_domain = _extract_domain(href)
        if not href_domain:
            continue
        if href not in seen:
            seen.add(href)
            links.append(href)
    return links

_legit_cache: Optional[list[str]] = None


def _load_legit_domains() -> list[str]:
    global _legit_cache
    if _legit_cache is not None:
        return _legit_cache
    domains: list[str] = []
    try:
        path = settings.LEGIT_DOMAINS_FILE
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip().lower()
                if line and not line.startswith("#"):
                    domains.append(line)
    except Exception as exc:
        logger.warning("Could not read legit domains file: %s", exc)
    _legit_cache = domains
    return _legit_cache


def reload_legit_domains() -> None:
    global _legit_cache
    _legit_cache = None


def is_legit_domain(domain: str, legit_domains: list[str]) -> bool:
    d = (domain or "").lower().removeprefix("www.")
    return any(d == g or d.endswith("." + g) for g in legit_domains)


def is_own_entity(domain: str, audited_domain: str, hreflang_alternates: set[str]) -> bool:
    d = (domain or "").lower().removeprefix("www.")
    base = (audited_domain or "").lower().removeprefix("www.")
    if base and (d == base or d.endswith("." + base)):
        return True
    return d in hreflang_alternates


def classify_outbound(html: str, source_url: str,
                      legit_domains: Optional[list[str]] = None) -> dict:
    """
    Return {"own_entity": [...], "legit": [...], "candidates": [...]} of
    distinct outbound domains. `legit_domains` defaults to the allowlist file.
    """
    legit = legit_domains if legit_domains is not None else _load_legit_domains()
    audited = _extract_domain(source_url)
    alternates = extract_hreflang_alternates(html)

    own: list[str] = []
    ok: list[str] = []
    candidates: list[str] = []
    seen: set[str] = set()

    for href in _extract_all_links_no_filter(html):
        d = _extract_domain(href)
        if not d or d in seen:
            continue
        # Skip the audited domain itself (exact match only; subdomains go to own_entity)
        if d == audited:
            continue
        seen.add(d)
        if is_own_entity(d, audited, alternates):
            own.append(d)
        elif is_legit_domain(d, legit):
            ok.append(d)
        else:
            candidates.append(d)

    return {"own_entity": own, "legit": ok, "candidates": candidates}

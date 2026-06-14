"""
Shared SEO data models.

These dataclasses define the provider-agnostic interface every SEO backend
(SEMrush, DataForSEO, …) returns, so the audit engine never depends on a
specific vendor. To add a new provider, implement a module exposing:

    get_domain_overview(domain)            -> DomainOverview
    get_backlinks_overview(domain)         -> BacklinksOverview
    get_organic_rankings(domain, limit)    -> OrganicRankings
    get_organic_keywords_for_terms(domain, terms) -> list[OrganicKeyword]

…returning these types, then register it in services/seo_service.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DomainOverview:
    domain: str
    authority_score: Optional[int] = None      # 0-100 (provider-normalised)
    organic_traffic: Optional[int] = None      # estimated monthly organic visits (all markets)
    organic_keywords: Optional[int] = None     # total keywords the domain ranks for
    paid_keywords: Optional[int] = None
    # Top traffic countries (provider DB/locale codes), highest traffic first,
    # plus the full per-country breakdown. Used to target ranking checks at the
    # site's real markets instead of a hardcoded default. Empty if the provider
    # doesn't expose per-country data.
    top_databases: list[str] = field(default_factory=list)
    traffic_by_country: dict = field(default_factory=dict)   # {db_code: organic_traffic}
    error: Optional[str] = None


@dataclass
class BacklinksOverview:
    domain: str
    total_backlinks: Optional[int] = None
    referring_domains: Optional[int] = None
    referring_ips: Optional[int] = None
    follow_links: Optional[int] = None
    nofollow_links: Optional[int] = None
    authority_score: Optional[int] = None      # 0-100 (provider-normalised)
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

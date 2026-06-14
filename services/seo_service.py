"""
Provider-agnostic SEO service — the single seam the audit engine talks to
(`from services import seo_service as semrush`).

Re-exports the shared SEO models and the four SEO functions from whichever
backend `settings.SEO_PROVIDER` selects:

    SEO_PROVIDER=semrush      -> services.semrush_service     (default)
    SEO_PROVIDER=dataforseo   -> services.dataforseo_service

Switching providers requires a restart (settings are read at startup).
"""

from __future__ import annotations

from config import settings

# Re-export shared types so callers can use `seo_service.OrganicKeyword`, etc.
from services.seo_models import (  # noqa: F401
    DomainOverview,
    BacklinksOverview,
    OrganicKeyword,
    OrganicRankings,
)

if settings.SEO_PROVIDER == "dataforseo":
    from services import dataforseo_service as _backend
else:
    from services import semrush_service as _backend

get_domain_overview = _backend.get_domain_overview
get_backlinks_overview = _backend.get_backlinks_overview
get_organic_rankings = _backend.get_organic_rankings
get_organic_keywords_for_terms = _backend.get_organic_keywords_for_terms
get_top_traffic_pages = _backend.get_top_traffic_pages

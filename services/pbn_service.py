"""
PBN / link-network detection service.

Builds a set of signals that, together, indicate a domain is part of a
Private Blog Network (PBN) or a link-selling farm rather than a genuine
business site:

  - backlinks without an audience (many referring domains, ~no organic traffic)
  - sitewide/templated linking (high backlinks-per-referring-domain)
  - topic mismatch (ranks for gambling/adult terms the homepage never mentions —
    the classic expired-domain / parasite-content tell)
  - link-network outbound pattern (homepage links out to many unrelated domains)
  - a very young domain carrying a large backlink profile
  - shared hosting / nameserver footprint with other audited domains (computed
    across the whole batch in audit_engine)

Network lookups use only free, key-less endpoints:
  - A record  : socket.gethostbyname
  - NS records: Google DNS-over-HTTPS (https://dns.google/resolve)
  - reg. date : RDAP (https://rdap.org/domain/<domain>)

`compute_signals(...)` returns a heuristic score + reasons; the final
LOW/MEDIUM/HIGH verdict is produced by the LLM in llm_service.assess_pbn().
"""

from __future__ import annotations

import logging
import socket
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 10


# ── Network footprint ──────────────────────────────────────────────────────────

def network_footprint(domain: str) -> dict:
    """Resolve the domain's IP (A record) and nameservers (NS) — free, no key."""
    out: dict = {"ip": None, "nameservers": [], "error": None}

    try:
        out["ip"] = socket.gethostbyname(domain)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.debug("IP lookup failed for %s: %s", domain, exc)

    try:
        resp = requests.get(
            "https://dns.google/resolve",
            params={"name": domain, "type": "NS"},
            timeout=_TIMEOUT,
        )
        if resp.ok:
            data = resp.json()
            ns = {
                a.get("data", "").rstrip(".").lower()
                for a in data.get("Answer", [])
                if a.get("type") == 2 and a.get("data")
            }
            out["nameservers"] = sorted(ns)
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
        logger.debug("NS lookup failed for %s: %s", domain, exc)

    return out


# ── Domain age (RDAP) ──────────────────────────────────────────────────────────

def domain_age(domain: str) -> dict:
    """Fetch the registration date + age in days via RDAP (free, no key)."""
    out: dict = {"created": None, "age_days": None, "registrar": None, "error": None}
    try:
        resp = requests.get(f"https://rdap.org/domain/{domain}", timeout=_TIMEOUT)
        if not resp.ok:
            out["error"] = f"RDAP HTTP {resp.status_code}"
            return out
        data = resp.json()

        for ev in data.get("events", []):
            if ev.get("eventAction") == "registration" and ev.get("eventDate"):
                out["created"] = ev["eventDate"]
                break

        for ent in data.get("entities", []):
            roles = ent.get("roles", [])
            if "registrar" in roles:
                for v in ent.get("vcardArray", [[], []])[1]:
                    if v and v[0] == "fn":
                        out["registrar"] = v[3]
                        break

        if out["created"]:
            try:
                created = datetime.fromisoformat(out["created"].replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                out["age_days"] = (datetime.now(timezone.utc) - created).days
            except Exception as exc:  # noqa: BLE001
                logger.debug("Could not parse RDAP date %s: %s", out["created"], exc)
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
        logger.debug("RDAP lookup failed for %s: %s", domain, exc)
    return out


# ── Heuristic signal scoring ───────────────────────────────────────────────────

def compute_signals(
    *,
    referring_domains: Optional[int],
    total_backlinks: Optional[int],
    organic_traffic: Optional[int],
    authority_score: Optional[int],
    pg_keyword_hit_count: int,
    homepage_text: str,
    distinct_external_domains: int,
    network: dict,
    age: dict,
    total_ranked_keywords: Optional[int] = None,
    reciprocal_links: Optional[list[dict]] = None,
    business_legitimacy: Optional[dict] = None,
) -> dict:
    """
    Compute a heuristic PBN score (0-100) plus human-readable reasons from
    signals we already collect. This is the prior the LLM verdict reasons over.
    """
    reasons: list[str] = []
    score = 0

    rd = referring_domains or 0
    tr = organic_traffic or 0
    bl = total_backlinks or 0
    ht = (homepage_text or "").lower()

    signals: dict = {
        "referring_domains": rd,
        "organic_traffic": tr,
        "total_backlinks": bl,
        "authority_score": authority_score,
        "porn_gambling_keyword_hits": pg_keyword_hit_count,
        "distinct_external_domains": distinct_external_domains,
        "domain_age_days": age.get("age_days"),
        "ip": network.get("ip"),
        "nameservers": network.get("nameservers", []),
        "registrar": age.get("registrar"),
        "reciprocal_strange_link_count": sum(1 for r in (reciprocal_links or []) if r.get("links_back")),
        "business_is_legit": (business_legitimacy or {}).get("is_legit"),
        "business_legitimacy_score": (business_legitimacy or {}).get("score"),
    }

    # 1. Backlinks without an audience.
    if rd >= 200 and tr < 1000:
        score += 30
        reasons.append(f"{rd} referring domains but only ~{tr} organic visits/mo — links without a real audience.")
    elif rd >= 100 and tr < 500:
        score += 18
        reasons.append(f"{rd} referring domains but only ~{tr} organic visits/mo.")

    # 1b. Link profile disproportionate to the site's authority.
    if rd >= 100 and authority_score and rd > authority_score * 10:
        score += 12
        reasons.append(
            f"{rd} referring domains against an authority score of {authority_score} — "
            f"a disproportionate link profile for a site this size."
        )

    # 2. Sitewide / templated linking.
    if rd > 0:
        ratio = bl / rd
        signals["backlinks_per_referring_domain"] = round(ratio, 1)
        if ratio >= 80:
            score += 10
            reasons.append(f"{ratio:.0f} backlinks per referring domain — sitewide/templated link placement.")

    # 3. Topic mismatch (the expired-domain / parasite tell) — judged by the
    #    SHARE of gambling/adult keywords, not the raw count. A handful of
    #    incidental matches among thousands of keywords is noise, not a signal.
    pg_ratio = (pg_keyword_hit_count / total_ranked_keywords) if total_ranked_keywords else None
    signals["total_ranked_keywords"] = total_ranked_keywords
    signals["porn_gambling_keyword_ratio"] = round(pg_ratio, 4) if pg_ratio is not None else None
    mentions = any(w in ht for w in ("casino", "gambl", "betting", "slot", "poker", "sex", "porn", "adult"))
    # "Substantial" = a meaningful share of the site's keywords, or a large
    # absolute count when the total keyword count is unknown.
    substantial = (
        (pg_ratio is not None and pg_ratio >= 0.05 and pg_keyword_hit_count >= 10)
        or (pg_ratio is None and pg_keyword_hit_count >= 25)
        or pg_keyword_hit_count >= 50
    )
    if substantial:
        pct = f"{pg_ratio * 100:.0f}%" if pg_ratio is not None else "many"
        if not mentions:
            score += 30
            reasons.append(
                f"Ranks for {pg_keyword_hit_count} gambling/adult keywords (~{pct} of its keywords) but the "
                f"homepage never mentions those topics — classic expired-domain or parasite-content footprint."
            )
        else:
            score += 12
            reasons.append(f"Gambling/adult keywords are a substantial share (~{pct}) of rankings.")
    # else: a few hits among many keywords → treated as noise, no points added.

    # 4. Reciprocal strange links — the core link-scheme tell. A strange,
    #    unrelated site that links BACK to this domain is a deliberate exchange.
    recip = [r for r in (reciprocal_links or []) if r.get("links_back")]
    if recip:
        # Reciprocated partners that are themselves NOT real businesses weigh more.
        non_legit = [r for r in recip if r.get("partner_legit") is False]
        score += 25 + min(10 * len(non_legit), 20)
        reasons.append(
            f"{len(recip)} strange site(s) link back to this domain"
            + (f", {len(non_legit)} of them not real businesses" if non_legit else "")
            + " — reciprocal link-scheme pattern."
        )

    # 4b. Business legitimacy of the audited site (dampener / riser).
    if business_legitimacy is not None:
        if business_legitimacy.get("is_legit"):
            score = max(0, score - 10)
            reasons.append("Audited site shows real business signals (contacts/address/schema).")
        elif business_legitimacy.get("score", 0) == 0:
            score += 12
            reasons.append("No business-legitimacy signals (no contacts, address, or org markup).")

    # 5. Young domain carrying a backlink profile.
    age_days = age.get("age_days")
    if age_days is not None:
        if age_days < 180 and rd >= 50:
            score += 15
            reasons.append(f"Domain registered only {age_days} days ago but already has {rd} referring domains.")
        elif age_days < 365:
            score += 6

    score = min(score, 100)
    if score >= 45:
        band = "HIGH"
    elif score >= 20:
        band = "MEDIUM"
    else:
        band = "LOW"

    return {"pbn_score": score, "pbn_heuristic_band": band, "signals": signals, "reasons": reasons}

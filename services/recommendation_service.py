"""
Partner recommendation logic (pure; no I/O).

Turns the audit's collected signals into a SKIP / CHECK_MANUALLY / APPROVED
verdict plus non-blocking flags. The SKIP/short-circuit decisions tied to
network fetches are made inline in audit_engine; this module supplies the
flag collection, the post-score decision, and the legacy risk_level mapping —
all unit-testable without network.
"""
from __future__ import annotations

from typing import Optional


def porn_gamble_hits(*, bad_link_hrefs: list[str], gambling_keyword_hrefs: list[str]) -> list[str]:
    """Deduplicated union of links to known/AI bad sites and gambling-keyword
    external links (already allowlist-filtered upstream). Non-empty → SKIP."""
    out: list[str] = []
    seen: set[str] = set()
    for h in [*bad_link_hrefs, *gambling_keyword_hrefs]:
        if h and h not in seen:
            seen.add(h)
            out.append(h)
    return out


def collect_flags(*, competitor_links: list, age_days: Optional[int],
                  organic_traffic: Optional[int], pbn_band: Optional[str],
                  content_farm_band: Optional[str], young_days: int, low_traffic: int) -> list[str]:
    """Non-blocking 'out of the ordinary' notes shown on APPROVED / CHECK_MANUALLY."""
    flags: list[str] = []
    if competitor_links:
        flags.append("Links to a competitor")
    if (age_days is not None and organic_traffic is not None
            and age_days < young_days and organic_traffic < low_traffic):
        flags.append(f"New domain (<{young_days}d) with low traffic (<{low_traffic}/mo)")
    if pbn_band == "MEDIUM":
        flags.append("Some PBN signals")
    if content_farm_band == "MEDIUM":
        flags.append("Some content-farm signals")
    return flags


def decide_after_scores(*, pbn_band: Optional[str], pbn_score, content_farm_band: Optional[str],
                        content_farm_score) -> tuple[str, str]:
    """Step 4/5: HIGH on either PBN or content-farm → CHECK_MANUALLY, else APPROVED."""
    reasons: list[str] = []
    if pbn_band == "HIGH":
        reasons.append(f"High PBN risk (score {pbn_score})")
    if content_farm_band == "HIGH":
        reasons.append(f"High content-farm risk (score {content_farm_score})")
    if reasons:
        return "CHECK_MANUALLY", "; ".join(reasons)
    return "APPROVED", ""


def derive_risk_level(decision: str) -> str:
    """Map the headline decision back to the legacy risk_level vocabulary so
    existing exports/summary code keeps working."""
    return {"SKIP": "HIGH", "CHECK_MANUALLY": "MEDIUM", "APPROVED": "LOW"}.get(decision, "UNKNOWN")


def build_phase_rows(steps: dict, niche: str) -> list[dict]:
    """
    Ordered rows for the results phase panel:
      [{"name", "status", "detail"}], status ∈ PASS|FAIL|SKIPPED|INFO|LOW|MEDIUM|HIGH.
    Phases not present in `steps` are shown SKIPPED with a reason derived from where
    the decision tree stopped.
    """
    steps = steps or {}
    rows: list[dict] = []

    hg = steps.get("homepage_gate") or {}
    hg_status = hg.get("status", "SKIPPED")
    rows.append({"name": "Homepage gate", "status": hg_status, "detail": hg.get("detail", "")})
    rows.append({"name": "Niche", "status": "INFO", "detail": niche or "—"})

    pg = steps.get("porn_gamble_links")
    if hg_status == "FAIL":
        skip_reason = "Skipped — didn't pass homepage check"
    elif pg and pg.get("status") == "FAIL":
        skip_reason = "Skipped — failed P/G links check"
    else:
        skip_reason = "Skipped — couldn't fetch data"

    if pg:
        detail = f"{pg.get('count', 0)} found" if pg.get("status") == "FAIL" else ""
        rows.append({"name": "P/G links", "status": pg.get("status", "SKIPPED"), "detail": detail})
    else:
        rows.append({"name": "P/G links", "status": "SKIPPED", "detail": skip_reason})

    for key, label in (("pbn", "PBN"), ("content_farm", "Spam / content farm")):
        ph = steps.get(key)
        if ph:
            rows.append({"name": label, "status": ph.get("band") or ph.get("status", "—"),
                         "detail": f"score {ph.get('score', 0)}"})
        else:
            rows.append({"name": label, "status": "SKIPPED", "detail": skip_reason})
    return rows

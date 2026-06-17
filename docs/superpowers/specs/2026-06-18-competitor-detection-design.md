# Spec: Competitor Detection vs. Your Own Business

**Date:** 2026-06-18
**Status:** Draft for review

## Background

The "is this site a competitor?" check (`ai_analysis.competitor_risk`) is **hardcoded to
"Bright Data"** — `llm_service._build_analyze_prompt` asks the LLM *"does this domain
directly compete with Bright Data?"* and frames the analyst as working "at Bright Data."
So it never compares against the actual user's business.

Separately, `data/competitor_sites.txt` exists but is only used by
`check_competitor_links` to flag sites that **link out to** competitors — it is NOT used
to decide whether the audited site **is** a competitor.

## Goal

Let the user define their own business and have the tool flag audited sites that compete
with it — using their **known competitor list** for definite matches and the **AI** to
discover unknown competitors by niche. Informational only (a flag + the existing
"Is a Competitor?" column); it does NOT change the Skip/Manual/Approved verdict.

## Non-goals (YAGNI)

- No SEMrush competitor overlap (AI niche comparison only, per decision).
- Competitor status does not gate the recommendation.

---

## 1. "Your business" config

- New file **`data/my_business.txt`** — one line: the user's own domain (lines starting
  with `#` are comments). Seed it with a comment + `brightdata.com` as the example.
- New setting `config/settings.py`: `MY_BUSINESS_FILE: Path = DATA_DIR / "my_business.txt"`.

## 2. Derive your niche once (cached)

New cached helper in `audit/audit_engine.py`:

```python
_my_business: Optional[dict] = None

def get_my_business() -> dict:
    """{"domain": str, "niche": str} for the user's own site. Domain from
    my_business.txt (fallback: settings.LINKBUILDING_TARGET_DOMAIN). Niche derived
    by scraping the homepage once + determine_niche. Cached for the process."""
```
- Reads the first non-comment line of `my_business.txt`; if missing/empty, falls back to
  `settings.LINKBUILDING_TARGET_DOMAIN`.
- Scrapes `https://{domain}` once (`bdata.scrape_page`), runs
  `ai_service.determine_niche(link_checker.extract_page_text(html))` → `niche`.
- Cached in `_my_business`; `reload_keywords()` resets it (add `_my_business = None`).
- On any failure, returns `{"domain": domain, "niche": ""}` (the AI can still infer from
  the domain name).

## 3. Known competitor check

New pure helper in `services/link_checker_service.py`:

```python
def is_listed_competitor(domain: str) -> bool:
    """True if `domain` is (or is a subdomain of) an entry in competitor_sites.txt."""
```
Reuses `_load_competitor_domains()` + `_is_match(host, comp_domain)`; strips `www.`.

## 4. AI discovery — parameterize the analysis prompt

`llm_service._build_analyze_prompt` and `analyze_audit` gain a `my_business: dict`
parameter (`{"domain","niche"}`, default `{}`). Replace the hardcoded "Bright Data"
references with the user's business:

- analyst framing → "You are a senior SEO and brand-safety analyst evaluating link
  partners for **{my_domain}** (niche: {my_niche})."
- `recommendation` → "recommendation for the **{my_domain}** team".
- `competitor_risk` → "boolean — does this domain directly compete with **{my_domain}**
  ({my_niche})? Judge by whether it is in the same or a directly overlapping
  business/niche."
- `brand_safe` → "safe to associate with **{my_domain}**".

If `my_business` is empty (no domain), fall back to neutral wording ("your business")
so nothing breaks. `analyze_audit`'s callers pass the cached `get_my_business()`.

## 5. Combine + store the verdict

In `audit_domain`, after the AI analysis:

```python
    _mb = get_my_business()
    # (passed into analyze_audit above)
    result.is_competitor = (
        link_checker.is_listed_competitor(domain)
        or bool(result.ai_analysis.get("competitor_risk"))
    )
    result.competitor_reason = (
        "On your competitor list" if link_checker.is_listed_competitor(domain)
        else (result.ai_analysis.get("competitor_reason", "") if result.is_competitor else "")
    )
```

New `AuditResult` fields: `is_competitor: bool = False`, `competitor_reason: str = ""`
(+ `to_dict` entries). Add a `competitor_reason` key to the analyze_audit output schema
(short reason string) so the AI explains an AI-detected match.

A competitor adds an **out-of-the-ordinary flag** to the recommendation (e.g.
`"Competitor — {reason}"`), shown on the detailed result. Does NOT change the decision.

## 6. UI

- The summary **"Is a Competitor?"** column reads `result.is_competitor` (instead of
  `ai_analysis.get("competitor_risk")`).
- The detailed result shows the competitor flag with `competitor_reason`.
- Sidebar: list `data/my_business.txt` alongside the other editable files (optional,
  consistent with how other files are shown).

## New surface area

- `data/my_business.txt`; `config/settings.py: MY_BUSINESS_FILE`.
- `audit/audit_engine.py`: `get_my_business()` (cached, reset on reload); `AuditResult`
  `is_competitor` / `competitor_reason`; wiring into `analyze_audit` + the combine step;
  competitor flag.
- `services/link_checker_service.py`: `is_listed_competitor`.
- `services/llm_service.py`: `_build_analyze_prompt` / `analyze_audit` `my_business`
  param + `competitor_reason` output key.
- `app.py`: competitor column/flag read `is_competitor`.

## Testing

- Unit: `is_listed_competitor` — exact + subdomain match against a stubbed
  `_COMPETITOR_DOMAINS`; non-match returns False; `www.` handling.
- The prompt parameterization + `get_my_business` (scrape/LLM) verified by import +
  full suite + a manual re-run (set `my_business.txt` to your domain; confirm an
  in-niche audited site is flagged and an unrelated one isn't).

## Open implementation questions (decide during plan)

- Whether to keep `LINKBUILDING_TARGET_DOMAIN` as the fallback domain (yes — minimal
  change) or fully migrate link-building to read `my_business.txt` (out of scope).
- Exact flag wording.

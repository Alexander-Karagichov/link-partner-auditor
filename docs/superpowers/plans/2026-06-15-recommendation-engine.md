# Partner Recommendation Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the rule-based `risk_level` verdict with a single headline recommendation — SKIP / CHECK_MANUALLY / APPROVED — produced by a short-circuiting decision tree, with per-step scores always visible and non-blocking flags.

**Architecture:** A new pure `recommendation_service` holds the decision/flag logic (unit-tested). `audit_domain` is reordered into a sequential tree: homepage gate → (data check) → deep-crawl porn/gamble-link check → PBN/content-farm → approve, stopping at the first failure. The old rule-based override block is removed; `risk_level` becomes a derived value so existing exports/summary keep working.

**Tech Stack:** Python 3, BeautifulSoup (lxml), Streamlit, pytest (already set up).

**Spec:** `docs/superpowers/specs/2026-06-15-recommendation-engine-design.md`

**Conventions (verified):**
- Run tests with `.venv/Scripts/python.exe -m pytest ...`. Use the **Bash** tool. Do NOT pip install.
- `link_checker_service`: `_extract_domain(href)` → lowercase netloc (strips `www.` via removeprefix); `BeautifulSoup`, `re` imported.
- `audit_engine` aliases: `link_checker`, `bdata`, `ai_service`, `semrush`, `pbn_service`; helpers `get_porn_gambling_keywords()`, `_homepage_gambling_gate(...)`. `from services import outbound_classifier as oc` is used locally in the gate.
- Result fields available: `bad_links_found` (list[{found_href,matched_bad_domain,link_text}]), `deep_page_checks` (list[{page_url,bad_links,keyword_flags,...}]), `competitor_links_found` (list[dict]), `organic_traffic` (int|None), `pbn` ({pbn_risk,pbn_score,...}), `content_farm` ({band,score,...}), `risk_level` (str).
- `domain_age_info` (local in audit_domain) = `{created, age_days, registrar, error}`.

---

## File Structure

**Create:**
- `services/recommendation_service.py` — pure decision/flag logic
- `tests/test_recommendation_service.py`
- `tests/test_gambling_keyword_links.py`

**Modify:**
- `services/link_checker_service.py` — add `gambling_keyword_external_links`
- `config/settings.py` — `RECO_YOUNG_DOMAIN_DAYS`, `RECO_LOW_TRAFFIC`
- `audit/audit_engine.py` — `AuditResult.recommendation` + `to_dict`; gate change; `audit_domain` reorder + decision tree; remove override block
- `app.py` — recommendation banner + scorecard + summary column
- `README.md` — document the recommendation

---

## Task 1: Structured gambling-keyword link detector

`keyword_links_present` returns descriptive strings (can't be allowlist-filtered). Add a structured detector returning hrefs, excluding internal links and allowlisted domains.

**Files:**
- Modify: `services/link_checker_service.py` (add after `keyword_links_present`)
- Test: `tests/test_gambling_keyword_links.py`

- [ ] **Step 1: Write failing test** — create `tests/test_gambling_keyword_links.py`:

```python
from services import link_checker_service as lc


def test_flags_gambling_anchor_to_unknown_external():
    html = """
    <a href="https://example.com/page">internal casino</a>
    <a href="https://bxk-media.io/x">play casino now</a>
    <a href="https://facebook.com/x">casino fanpage</a>
    <a href="https://news.com/economy">economy report</a>
    """
    out = lc.gambling_keyword_external_links(
        html, ["casino", "porn"], source_domain="example.com",
        legit_domains=["facebook.com"],
    )
    assert "https://bxk-media.io/x" in out      # gambling anchor → unknown external
    assert not any("example.com" in h for h in out)   # internal excluded
    assert not any("facebook.com" in h for h in out)  # allowlisted excluded
    assert not any("news.com" in h for h in out)      # no gambling keyword


def test_empty_inputs():
    assert lc.gambling_keyword_external_links("", ["casino"], "x", []) == []
    assert lc.gambling_keyword_external_links("<a href='/x'>hi</a>", [], "x", []) == []
```

- [ ] **Step 2: Run it, verify fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_gambling_keyword_links.py -v`
Expected: FAIL — function not defined.

- [ ] **Step 3: Implement** — add to `services/link_checker_service.py` after `keyword_links_present`:

```python
def gambling_keyword_external_links(html: str, keywords: list[str], source_domain: str,
                                    legit_domains: list[str]) -> list[str]:
    """
    Return hrefs of EXTERNAL links whose anchor text or href contains a
    gambling/adult keyword, EXCLUDING internal links and links to allowlisted
    (legit) domains. A gambling-keyword anchor pointing to an unrecognized
    external site is a strong 'links to porn/gamble' signal even when the domain
    name itself is opaque. Deduplicated.
    """
    if not html or not keywords:
        return []
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return []
    norm_source = (source_domain or "").lower().removeprefix("www.")
    legit = [g.lower().removeprefix("www.") for g in (legit_domains or [])]
    patterns = [re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords]
    out: list[str] = []
    seen: set[str] = set()
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        d = _extract_domain(href)
        if not d:
            continue
        if norm_source and (d == norm_source or d.endswith("." + norm_source)):
            continue  # internal
        if any(d == g or d.endswith("." + g) for g in legit):
            continue  # allowlisted legit destination
        text = tag.get_text(strip=True)
        if any(p.search(f"{href} {text}") for p in patterns):
            if href not in seen:
                seen.add(href)
                out.append(href)
    return out
```

- [ ] **Step 4: Run it, verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_gambling_keyword_links.py -v`
Expected: 2 passed. Then full suite `.venv/Scripts/python.exe -m pytest -q` (expect prior + 2).

- [ ] **Step 5: Commit**

```bash
git add services/link_checker_service.py tests/test_gambling_keyword_links.py
git commit -m "feat: structured allowlist-filtered gambling-keyword link detector"
```

---

## Task 2: recommendation_service (pure decision logic)

**Files:**
- Create: `services/recommendation_service.py`
- Test: `tests/test_recommendation_service.py`

- [ ] **Step 1: Write failing tests** — create `tests/test_recommendation_service.py`:

```python
from services import recommendation_service as rec


def test_porn_gamble_hits_dedup_union():
    out = rec.porn_gamble_hits(
        bad_link_hrefs=["https://a.com", "https://b.com"],
        gambling_keyword_hrefs=["https://b.com", "https://c.com"],
    )
    assert out == ["https://a.com", "https://b.com", "https://c.com"]


def test_collect_flags_all_conditions():
    flags = rec.collect_flags(
        competitor_links=[{"x": 1}], age_days=90, organic_traffic=500,
        pbn_band="MEDIUM", content_farm_band="MEDIUM",
        young_days=180, low_traffic=1000,
    )
    assert any("competitor" in f.lower() for f in flags)
    assert any("new domain" in f.lower() for f in flags)
    assert any("pbn" in f.lower() for f in flags)
    assert any("content-farm" in f.lower() for f in flags)


def test_young_thin_needs_both():
    # young but NOT low traffic → no flag
    assert not any("new domain" in f.lower() for f in rec.collect_flags(
        competitor_links=[], age_days=90, organic_traffic=5000,
        pbn_band="LOW", content_farm_band="LOW", young_days=180, low_traffic=1000))
    # low traffic but NOT young → no flag
    assert not any("new domain" in f.lower() for f in rec.collect_flags(
        competitor_links=[], age_days=900, organic_traffic=100,
        pbn_band="LOW", content_farm_band="LOW", young_days=180, low_traffic=1000))


def test_decide_after_scores():
    assert rec.decide_after_scores(pbn_band="HIGH", pbn_score=80,
                                   content_farm_band="LOW", content_farm_score=5)[0] == "CHECK_MANUALLY"
    assert rec.decide_after_scores(pbn_band="LOW", pbn_score=5,
                                   content_farm_band="HIGH", content_farm_score=70)[0] == "CHECK_MANUALLY"
    d, r = rec.decide_after_scores(pbn_band="MEDIUM", pbn_score=45,
                                   content_farm_band="LOW", content_farm_score=5)
    assert d == "APPROVED" and r == ""


def test_derive_risk_level():
    assert rec.derive_risk_level("SKIP") == "HIGH"
    assert rec.derive_risk_level("CHECK_MANUALLY") == "MEDIUM"
    assert rec.derive_risk_level("APPROVED") == "LOW"
```

- [ ] **Step 2: Run them, verify fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_recommendation_service.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** — create `services/recommendation_service.py`:

```python
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
```

- [ ] **Step 4: Run them, verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_recommendation_service.py -v`
Expected: 5 passed. Then full suite green.

- [ ] **Step 5: Commit**

```bash
git add services/recommendation_service.py tests/test_recommendation_service.py
git commit -m "feat: recommendation_service pure decision logic"
```

---

## Task 3: Config knobs

**Files:**
- Modify: `config/settings.py`

- [ ] **Step 1: Add settings** — after the content-farm config block, add:

```python
# ── Partner recommendation ────────────────────────────────────────────────────
RECO_YOUNG_DOMAIN_DAYS: int = int(os.getenv("RECO_YOUNG_DOMAIN_DAYS", "180"))   # "<6 months" flag
RECO_LOW_TRAFFIC: int = int(os.getenv("RECO_LOW_TRAFFIC", "1000"))              # low-traffic flag
```

- [ ] **Step 2: Verify**

Run: `.venv/Scripts/python.exe -c "from config import settings as s; print(s.RECO_YOUNG_DOMAIN_DAYS, s.RECO_LOW_TRAFFIC)"`
Expected: `180 1000`. Then full suite green.

- [ ] **Step 3: Commit**

```bash
git add config/settings.py
git commit -m "feat: recommendation config knobs"
```

---

## Task 4: AuditResult.recommendation field + to_dict

**Files:**
- Modify: `audit/audit_engine.py` (`AuditResult` dataclass + `to_dict`)

- [ ] **Step 1: Add field** — after the `content_farm` field, add:

```python
    # ── Headline recommendation (Skip / Check manually / Approved) ─────────────
    recommendation: dict = field(default_factory=dict)   # {decision, reason, flags, steps}
```

- [ ] **Step 2: Add to_dict entries** — after the `content_farm` entries, add:

```python
            "recommendation": self.recommendation,
            "recommendation_decision": self.recommendation.get("decision"),
            "recommendation_reason": self.recommendation.get("reason"),
```

- [ ] **Step 3: Verify**

Run: `.venv/Scripts/python.exe -c "from audit.audit_engine import AuditResult; r=AuditResult(domain='x', input_url='http://x'); d=r.to_dict(); print(d['recommendation'], d['recommendation_decision'])"`
Expected: `{} None`. Then full suite green.

- [ ] **Step 4: Commit**

```bash
git add audit/audit_engine.py
git commit -m "feat: AuditResult.recommendation field"
```

---

## Task 5: Add homepage gambling-keyword signal to the gate

**Files:**
- Modify: `audit/audit_engine.py` (`_homepage_gambling_gate`)

Read `_homepage_gambling_gate` first. It does: (1) known-bad `check_links` match → return early; (2) `oc.classify_outbound` + `ai_service.classify_link_partners` → flag `gambling_porn`. It returns `(failed, offending, reason, buckets, verdicts)`. `get_porn_gambling_keywords()` and `oc` (outbound_classifier) are available.

- [ ] **Step 1: Insert the keyword check BETWEEN the known-bad block and the AI block** (so two free checks precede the paid AI call). After the `if offending: return True, offending, "Homepage links to a known gambling/adult site.", {}, []` line, add:

```python
    # 1b. Gambling/adult keyword anchor → non-allowlisted external link (free).
    _kw_links = link_checker.gambling_keyword_external_links(
        html, get_porn_gambling_keywords(), domain, oc._load_legit_domains()
    )
    if _kw_links:
        for _h in _kw_links:
            offending.append({"found_href": _h, "matched_bad_domain": "[gambling-keyword anchor]", "link_text": ""})
        return True, offending, "Homepage links to a gambling/adult site (keyword anchor).", {}, []
```
Note: `oc` is imported locally at the top of this function (`from services import outbound_classifier as oc`). Confirm that import is present at the function top; if it's only imported later, move/ensure it's available before this block.

- [ ] **Step 2: Verify (free path, no network)** — `royalcasino.dk` is in `data/known_bad_sites.txt`; use a keyword-anchor link to an unknown site:

```bash
.venv/Scripts/python.exe -c "
from audit import audit_engine as ae
html='<a href=\"https://bxk-media.io/x\">play casino now</a>'
r = ae._homepage_gambling_gate('example.com', html)
print(r[0], r[2])
"
```
Expected: `True Homepage links to a gambling/adult site (keyword anchor).`
(This exercises the free known-bad + keyword path; the AI block is not reached because the keyword check returns first.)

Then full suite green.

- [ ] **Step 3: Commit**

```bash
git add audit/audit_engine.py
git commit -m "feat: gate flags homepage gambling-keyword anchor links"
```

---

## Task 6: audit_domain — reorder into the decision tree (the big one)

**Files:**
- Modify: `audit/audit_engine.py` (`audit_domain`)

**Goal:** reorder so the deep-page crawl runs BEFORE the PBN/reciprocity/content-farm work, add the porn/gamble-link short-circuit, remove the old rule-based override block, build `result.recommendation`, and gate the anchor on APPROVED.

Read `audit_domain` fully first. Current order: gate(383) → Wave1(394) → Wave2 SEMrush(424) → homepage processing(~470) → reciprocity/legitimacy(494) → content-farm(539) → SERP results(622) → deep crawl(628) → AI+PBN(739) → risk overrides 7b(798–864) → anchor 8(866).

**Target order:**
1. Gate (unchanged; on fail it already sets SKIP — see Step 4 below for the reason wording).
2. Wave 1 + Wave 2 + homepage processing (unchanged).
3. **Data-sufficiency check** (NEW): right after the homepage `if html:` block, if `not html` set recommendation CHECK_MANUALLY and return (see Step 2).
4. **SERP-results assignment + deep-page crawl** — MOVE the `# ── SERP results (Bright Data Google site: checks)` assignment block (`result.serp_porn_gambling_results = serp_results`, `serp_core...`) AND the entire `# ── 6b: Deep page link check` section to here (right after homepage processing / data check), BEFORE the reciprocity block. The deep crawl reads `result.serp_porn_gambling_results`, so the SERP assignment MUST precede it. `serp_results`/`serp_core_results` are already in scope from Wave 1.
5. **Step 3 porn/gamble short-circuit** (NEW) — after the deep crawl, aggregate bad-link + gambling-keyword hrefs (homepage + deep) and, if non-empty, set recommendation SKIP and return (see Step 3).
6. **Reciprocity/legitimacy + content-farm + AI/PBN** — these now run only if not skipped (they're already after this point once the deep crawl moves up; keep their internals).
7. **Build recommendation** (NEW) — replace the entire `# ── 7b: Rule-based risk overrides` block with the recommendation assembly (Step 5).
8. **Anchor (section 8)** — wrap so it only runs when `result.recommendation["decision"] == "APPROVED"` (Step 6).

Because this relocates blocks, work carefully and keep each moved block's internals identical. If the structure differs from this description, STOP and report NEEDS_CONTEXT.

- [ ] **Step 1: Add gambling-keyword capture to the deep crawl.** Inside `_deep_check_page` (the nested function in the deep-crawl block), where it computes `keyword_flags`, also add:

```python
                check_entry["gambling_keyword_links"] = link_checker.gambling_keyword_external_links(
                    page_html, porn_kws, domain, oc_legit
                )
```
At the top of `audit_domain`, near where `porn_kws` is defined, add:

```python
    from services import outbound_classifier as _oc
    oc_legit = _oc._load_legit_domains()
```
`oc_legit` is then in scope for the nested `_deep_check_page` (closure) and for Step 3.

- [ ] **Step 2: Data-sufficiency check** — right after the homepage processing `if html:` block ends, add:

```python
    # ── Decision: data sufficiency ────────────────────────────────────────────
    if not html:
        from services import recommendation_service as rec
        result.recommendation = {
            "decision": "CHECK_MANUALLY",
            "reason": "Couldn't fetch homepage",
            "flags": [],
            "steps": {"homepage_gate": {"status": "FAIL", "detail": result.scrape_error or "no HTML"}},
        }
        result.risk_level = rec.derive_risk_level("CHECK_MANUALLY")
        logger.info("[%s] Recommendation: CHECK_MANUALLY (no homepage).", domain)
        return result
```

- [ ] **Step 3: Move the deep crawl up, then add the Step-3 short-circuit** after it:

```python
    # ── Decision: porn/gamble outbound links → SKIP ───────────────────────────
    from services import recommendation_service as rec
    _bad_hrefs = [b.get("found_href", "") for b in result.bad_links_found]
    for _c in result.deep_page_checks:
        _bad_hrefs += [b.get("found_href", "") for b in _c.get("bad_links", [])]
    _kw_hrefs = link_checker.gambling_keyword_external_links(html, porn_kws, domain, oc_legit)
    for _c in result.deep_page_checks:
        _kw_hrefs += _c.get("gambling_keyword_links", [])
    _pg_hits = rec.porn_gamble_hits(bad_link_hrefs=_bad_hrefs, gambling_keyword_hrefs=_kw_hrefs)
    if _pg_hits:
        result.recommendation = {
            "decision": "SKIP",
            "reason": "Linking to porn/gamble websites",
            "flags": [],
            "steps": {
                "homepage_gate": {"status": "PASS", "detail": ""},
                "porn_gamble_links": {"status": "FAIL", "count": len(_pg_hits), "examples": _pg_hits[:5]},
            },
        }
        result.risk_level = rec.derive_risk_level("SKIP")
        logger.info("[%s] Recommendation: SKIP (%d porn/gamble link(s)).", domain, len(_pg_hits))
        return result
```

- [ ] **Step 4: Update the GATE early-return** (Step 1's hard fail) to set a recommendation instead of `risk_level = "BAD"`. Find the gate fail block (`if failed:` after `_homepage_gambling_gate`) and replace its body with:

```python
        if failed:
            from services import recommendation_service as rec
            result.homepage_scraped = True
            result.bad_links_found = offending
            result.early_failed = True
            result.early_fail_reason = reason
            result.recommendation = {
                "decision": "SKIP",
                "reason": "Failed homepage check",
                "flags": [],
                "steps": {"homepage_gate": {"status": "FAIL", "detail": reason}},
            }
            result.risk_level = rec.derive_risk_level("SKIP")
            result.ai_analysis = {"summary": reason, "risk_level": "HIGH"}
            logger.warning("[%s] SKIP at homepage gate: %s", domain, reason)
            return result
```

- [ ] **Step 5: Replace the entire `# ── 7b: Rule-based risk overrides` block (Rules 1–3, through the content-farm nudge) with the recommendation assembly:**

```python
    # ── Build the headline recommendation (Skip already handled by short-circuits) ─
    _pbn_band = (result.pbn or {}).get("pbn_risk")
    _pbn_score = (result.pbn or {}).get("pbn_score", 0)
    _cf_band = (result.content_farm or {}).get("band")
    _cf_score = (result.content_farm or {}).get("score", 0)

    _flags = rec.collect_flags(
        competitor_links=result.competitor_links_found,
        age_days=(domain_age_info or {}).get("age_days"),
        organic_traffic=result.organic_traffic,
        pbn_band=_pbn_band, content_farm_band=_cf_band,
        young_days=settings.RECO_YOUNG_DOMAIN_DAYS, low_traffic=settings.RECO_LOW_TRAFFIC,
    )
    _decision, _reason = rec.decide_after_scores(
        pbn_band=_pbn_band, pbn_score=_pbn_score,
        content_farm_band=_cf_band, content_farm_score=_cf_score,
    )
    result.recommendation = {
        "decision": _decision,
        "reason": _reason,
        "flags": _flags,
        "steps": {
            "homepage_gate": {"status": "PASS", "detail": ""},
            "porn_gamble_links": {"status": "PASS", "count": 0, "examples": []},
            "pbn": {"status": "FAIL" if _pbn_band == "HIGH" else "PASS", "band": _pbn_band, "score": _pbn_score},
            "content_farm": {"status": "FAIL" if _cf_band == "HIGH" else "PASS",
                             "band": _cf_band, "score": _cf_score,
                             "semrush_checked": (result.content_farm or {}).get("semrush_checked", False)},
        },
    }
    result.risk_level = rec.derive_risk_level(_decision)
    logger.info("[%s] Recommendation: %s%s", domain, _decision, f" — {_reason}" if _reason else "")
```
Note: `result.ai_analysis`/`result.risk_level = analysis.get(...)` line ABOVE this (where the AI analysis is stored) stays — but the AI's `risk_level` is now overwritten by the derived value here. Keep `result.ai_analysis = analysis` (for the summary text); the `result.risk_level = analysis.get("risk_level", "UNKNOWN")` line can stay (it's overwritten below) or be removed — leave it, it's harmless.

- [ ] **Step 6: Gate the anchor on APPROVED.** In section 8 (link building recommendation), change the guard from `if lb_targets:` to:

```python
    if lb_targets and result.recommendation.get("decision") == "APPROVED":
```
(Leave the `else` branch logging as-is; add an elif/else note is optional.)

- [ ] **Step 7: Verify**

1. `.venv/Scripts/python.exe -c "import audit.audit_engine; print('ok')"` → `ok`
2. Full suite: `.venv/Scripts/python.exe -m pytest -q` → all green.
3. Grep guards: `grep -n "decision\"\] == \"APPROVED\"\|recommendation.get(\"decision\")" audit/audit_engine.py` shows the anchor guard.
4. Confirm the old override block is gone: `grep -n "OVERRIDE → NO_RISK\|OVERRIDE → CRITICAL" audit/audit_engine.py` → no output.

- [ ] **Step 8: Commit**

```bash
git add audit/audit_engine.py
git commit -m "feat: audit_domain decision tree (Skip/Manual/Approved) replacing risk overrides"
```

## Escalation
If relocating the deep-crawl/reciprocity/content-farm blocks is ambiguous or risks breaking variable scope (`porn_kws`, `oc_legit`, `domain_age_info`, `serp_results`), STOP and report NEEDS_CONTEXT with the actual code. This task must not break the existing flow.

---

## Task 7: UI — recommendation banner + scorecard + summary column

**Files:**
- Modify: `app.py`

Read `app.py` around: the per-result render (the `risk = result.risk_level` / `**Risk: ...**` block ~line 319–333), the existing PBN/content-farm banners (~350–390), and the summary-table row builder (~line 140, `"Risk": r.risk_level`).

- [ ] **Step 1: Recommendation banner** — at the very start of the per-result render (before the existing `**Risk:**` markdown), add:

```python
        _reco = getattr(result, "recommendation", None) or {}
        _decision = _reco.get("decision")
        if _decision:
            _r_emoji = {"SKIP": "🔴", "CHECK_MANUALLY": "🟠", "APPROVED": "🟢"}.get(_decision, "❓")
            _r_label = {"SKIP": "SKIP", "CHECK_MANUALLY": "CHECK MANUALLY", "APPROVED": "APPROVED"}.get(_decision, _decision)
            st.markdown(f"## {_r_emoji} {_r_label}" + (f" — {_reco.get('reason')}" if _reco.get("reason") else ""))
            for _flag in (_reco.get("flags") or []):
                st.caption(f"⚠️ {_flag}")
            _steps = _reco.get("steps") or {}
            _sc = []
            if "homepage_gate" in _steps: _sc.append(f"Homepage: {_steps['homepage_gate'].get('status')}")
            if "porn_gamble_links" in _steps: _sc.append(f"P/G links: {_steps['porn_gamble_links'].get('status')} ({_steps['porn_gamble_links'].get('count', 0)})")
            if "pbn" in _steps and _steps["pbn"].get("band"): _sc.append(f"PBN: {_steps['pbn'].get('band')} ({_steps['pbn'].get('score')})")
            if "content_farm" in _steps and _steps["content_farm"].get("band"): _sc.append(f"Content-farm: {_steps['content_farm'].get('band')} ({_steps['content_farm'].get('score')})")
            if _sc:
                st.caption("  •  ".join(_sc))
            st.divider()
```

- [ ] **Step 2: Summary table column** — in the row dict (where `"Risk": r.risk_level` is set, ~line 140), add a Recommendation field right before `"Risk"`:

```python
            "Recommendation": (r.recommendation.get("decision") if getattr(r, "recommendation", None) else ""),
```

- [ ] **Step 3: Verify**

1. `.venv/Scripts/python.exe -m py_compile app.py` → no error.
2. `.venv/Scripts/python.exe -c "import ast; ast.parse(open('app.py',encoding='utf-8').read()); print('parse ok')"` → `parse ok`.
3. Full suite green. Do NOT launch streamlit.

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat: UI recommendation banner, scorecard, summary column"
```

---

## Task 8: Docs + full regression

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Full regression** — `.venv/Scripts/python.exe -m pytest -q`. Expect all green. If anything fails, STOP and report BLOCKED.

- [ ] **Step 2: Document** — read `README.md`, then add a concise "Recommendation (Skip / Check manually / Approved)" subsection near the risk/scoring docs: the short-circuiting decision tree (homepage gate → porn/gamble links → data check → PBN/spam HIGH → approved), the flags (competitor link; young <6mo AND <1k traffic; PBN/content-farm MEDIUM), that every step's score is always shown, that the anchor is generated only for Approved, and that `risk_level` is now derived from the decision. Mention env vars `RECO_YOUNG_DOMAIN_DAYS`, `RECO_LOW_TRAFFIC`.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document partner recommendation engine"
```

---

## Self-Review Notes (for the implementer)

- **Task 6 is the risk.** Relocating deep-crawl ahead of reciprocity/content-farm is the crux: the short-circuit only saves cost if the porn/gamble check runs BEFORE those. Keep moved blocks' internals identical; only change ordering + add the decision points.
- **`oc_legit`** (the legit allowlist) must be loaded once near the top of `audit_domain` and reused by the gate-adjacent keyword detection in the deep crawl and Step 3.
- **risk_level is derived**, so the summary table / RISK_ORDER / highlight styling keep working unchanged (SKIP→HIGH, MANUAL→MEDIUM, APPROVED→LOW all exist in RISK_ORDER).
- **Anchor gating**: only APPROVED generates the link recommendation; SKIP/MANUAL return before it (Steps 2–4) or are gated (Step 6).
- **Flags don't change the decision** — they're attached after `decide_after_scores`. A MEDIUM PBN is APPROVED + flag, not manual.

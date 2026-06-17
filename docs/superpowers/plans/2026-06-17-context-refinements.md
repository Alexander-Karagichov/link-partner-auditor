# Context Refinements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop false-skipping/flagging legit businesses — verify gambling-link Skips with an AI promoter-vs-incidental check, drop other-subdomain pages from the crawl, fix the WARN skip-reason, and sharpen the content-farm prompt so service pages aren't trivia.

**Architecture:** Two pure helpers (`same_site`, a `build_phase_rows` reason fix) in `recommendation_service`; one new LLM function + one prompt sharpen in `llm_service`; `audit_domain` filters the deep-crawl page list to the exact domain and runs the promoter check at the Skip threshold.

**Tech Stack:** Python 3, pytest. LLM via `_backend.chat_json` + `_parse_json`.

**Spec:** `docs/superpowers/specs/2026-06-17-context-refinements-design.md`

**Conventions:** run tests with `.venv/Scripts/python.exe -m pytest ...`; use the **Bash** tool; do NOT pip install. `recommendation_service` already has `_domain_of(href)` (strips `www.`).

---

## Task 1: recommendation_service — `same_site` + WARN skip-reason

**Files:**
- Modify: `services/recommendation_service.py`
- Test: `tests/test_context_refine.py`

- [ ] **Step 1: Write failing tests** — create `tests/test_context_refine.py`:

```python
from services import recommendation_service as rec


def test_same_site():
    assert rec.same_site("https://xavor.com/x", "xavor.com") is True
    assert rec.same_site("https://www.xavor.com/x", "xavor.com") is True
    assert rec.same_site("https://china.xavor.com/x", "xavor.com") is False
    assert rec.same_site("https://notxavor.com/x", "xavor.com") is False


def test_skip_reason_warn_is_manual_review():
    steps = {
        "homepage_gate": {"status": "PASS", "detail": ""},
        "porn_gamble_links": {"status": "WARN", "count": 1, "examples": ["a.com"]},
    }
    rows = rec.build_phase_rows(steps, "Software")
    pbn = next(r for r in rows if r["name"] == "PBN")
    assert "manual review" in pbn["detail"] and "couldn't fetch" not in pbn["detail"]
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_context_refine.py -v`
Expected: `test_same_site` fails (no `same_site`); `test_skip_reason_warn_is_manual_review` fails (WARN → "couldn't fetch data").

- [ ] **Step 3: Add `same_site`** to `services/recommendation_service.py` (after `_domain_of`):

```python
def same_site(url: str, audited_domain: str) -> bool:
    """True iff url's host is the exact audited domain or its www. form (NOT other subdomains)."""
    base = (audited_domain or "").lower().removeprefix("www.")
    return bool(base) and _domain_of(url) == base
```

- [ ] **Step 4: Fix the WARN skip-reason in `build_phase_rows`.** Replace the skip-reason ladder:

```python
    if hg_status == "FAIL":
        skip_reason = "Skipped — didn't pass homepage check"
    elif pg and pg.get("status") == "FAIL":
        skip_reason = "Skipped — failed P/G links check"
    else:
        skip_reason = "Skipped — couldn't fetch data"
```
with:
```python
    if hg_status == "FAIL":
        skip_reason = "Skipped — didn't pass homepage check"
    elif pg and pg.get("status") == "FAIL":
        skip_reason = "Skipped — failed P/G links check"
    elif pg and pg.get("status") == "WARN":
        skip_reason = "Skipped — porn/gamble check sent to manual review"
    else:
        skip_reason = "Skipped — couldn't fetch data"
```

- [ ] **Step 5: Run, verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_context_refine.py -v`
Expected: 2 passed. Then full suite `.venv/Scripts/python.exe -m pytest -q` (expect 31).

- [ ] **Step 6: Commit**

```bash
git add services/recommendation_service.py tests/test_context_refine.py
git commit -m "feat: same_site helper + WARN skip-reason in phase panel"
```

---

## Task 2: llm_service — promoter context check + sharpened article prompt

**Files:**
- Modify: `services/llm_service.py`

- [ ] **Step 1: Add `classify_gambling_link_context`** (place after `classify_link_partners` or near the other classifiers):

```python
def classify_gambling_link_context(audited_domain: str, niche: str,
                                   source_pages: list[str], gambling_domains: list[str]) -> str:
    """
    Decide whether a site that links to gambling domains is a PROMOTER (affiliate /
    casino-review / gambling-content site) or an INCIDENTAL linker (neutral business
    directory, news/media, B2B tool, or safety/educational site). Returns "promoter"
    or "incidental"; defaults to "incidental" on error (so failures route to human
    review, never a false auto-skip).
    """
    try:
        pages_block = "\n".join(f"- {p}" for p in (source_pages or [])[:15]) or "(homepage)"
        doms_block = ", ".join((gambling_domains or [])[:20])
        prompt_parts = [
            f"The website {audited_domain} (niche: {niche or 'unknown'}) links out to these "
            f"gambling/casino/betting/lottery domains: {doms_block}.",
            "Those links were found on these pages of the site:",
            pages_block,
            "",
            "## Task",
            "Decide whether this site is a GAMBLING PROMOTER — a casino/betting affiliate, a "
            "gambling-review site, or a gambling-content site that exists to push gambling — OR "
            "an INCIDENTAL linker: a neutral business directory / company database (company "
            "profiles that happen to include casinos or lotteries), a news/media outlet, a B2B "
            "tool, or a safety/educational site that links to gambling companies incidentally.",
            "Government lotteries and casino COMPANIES profiled in a business directory are INCIDENTAL.",
            'Return ONLY JSON: {"context": "promoter"} or {"context": "incidental"}.',
        ]
        system = "You are a brand-safety analyst. You always respond with valid JSON only."
        raw = _backend.chat_json(system, "\n".join(prompt_parts), max_tokens=80)
        parsed = _parse_json(raw)
        return "promoter" if str(parsed.get("context", "")).strip().lower() == "promoter" else "incidental"
    except Exception:
        logger.exception("classify_gambling_link_context failed")
        return "incidental"
```

- [ ] **Step 2: Sharpen `classify_article_quality`.** In its `prompt_parts`, insert this string item immediately BEFORE the `'Return ONLY JSON with key "results": ...'` line:

```python
            "A legitimate business's service / product / solution / landing / pricing / about / "
            "contact pages are NOT content-farm filler — do NOT mark them is_trivia. Only flag "
            "genuine low-value INFORMATIONAL trivia/SEO-bait articles (how-to listicles, 'what is "
            "X' definitions, unit conversions, generic filler with no expertise or commercial purpose).",
```
Match indentation; keep surrounding items' trailing commas.

- [ ] **Step 3: Verify**

Run: `.venv/Scripts/python.exe -c "from services import llm_service as m; print(hasattr(m,'classify_gambling_link_context'))"`
Expected: `True`. Then `.venv/Scripts/python.exe -m pytest -q` → 31 passed.

- [ ] **Step 4: Commit**

```bash
git add services/llm_service.py
git commit -m "feat: gambling-link context check + service-page-aware article prompt"
```

---

## Task 3: audit_domain — subdomain filter + promoter check

**Files:**
- Modify: `audit/audit_engine.py`

`rec` (recommendation_service) and `ai_service` (llm_service) are already imported/aliased in `audit_domain`. `full_url`, `domain`, `result.niche` are in scope.

- [ ] **Step 1: Drop other-subdomain pages from the deep crawl.** Find where `pg_ranking_urls` is built (`pg_ranking_urls = list(dict.fromkeys(_semrush_pages + _serp_pages))`). Immediately AFTER that line (before the `MAX_DEEP_PAGES_PER_DOMAIN` cap), add:

```python
    # Google treats subdomains as separate sites: only deep-check pages on the exact
    # audited domain (china.xavor.com gambling content must not fail xavor.com).
    _before = len(pg_ranking_urls)
    pg_ranking_urls = [u for u in pg_ranking_urls if rec.same_site(u, domain)]
    if len(pg_ranking_urls) != _before:
        logger.info("[%s] Dropped %d subdomain page(s) from deep crawl.", domain, _before - len(pg_ranking_urls))
```
Note: `rec` must be imported above this point in `audit_domain`. It is imported near the gate; if a `NameError` risk exists, add `from services import recommendation_service as rec` near the top of `audit_domain`.

- [ ] **Step 2: Add the promoter check at the SKIP threshold.** Find the porn/gamble decision block:

```python
    _pg_domains = rec.confirmed_pg_domains(result.bad_links_found, result.deep_page_checks)
    _pg_decision, _pg_reason, _pg_flag = rec.decide_porn_gamble(_pg_domains, settings.PORN_GAMBLE_SKIP_THRESHOLD)
    if _pg_decision:
```
Insert BETWEEN the `decide_porn_gamble(...)` line and the `if _pg_decision:` line:

```python
    if _pg_decision == "SKIP":
        # Verify it's a gambling promoter, not a neutral directory/news/B2B that links
        # to gambling companies incidentally (e.g. clodura.ai's /directory/company/ pages).
        _src_pages: list[str] = [c["page_url"] for c in result.deep_page_checks if c.get("bad_links")]
        if result.bad_links_found:
            _src_pages = [full_url] + _src_pages
        _ctx = ai_service.classify_gambling_link_context(domain, getattr(result, "niche", "") or "", _src_pages, _pg_domains)
        if _ctx == "incidental":
            _pg_decision = "CHECK_MANUALLY"
            _pg_reason = f"Links to {len(_pg_domains)} gambling sites (likely incidental — review)"
            _pg_flag = f"Incidental gambling links ({len(_pg_domains)}): {', '.join(_pg_domains[:5])}"
            logger.info("[%s] Gambling links judged INCIDENTAL — downgraded SKIP → manual.", domain)
```

- [ ] **Step 3: Verify**

1. `.venv/Scripts/python.exe -c "import audit.audit_engine; print('ok')"` → `ok`
2. `grep -n "same_site\|classify_gambling_link_context" audit/audit_engine.py` → shows both used.
3. `.venv/Scripts/python.exe -m pytest -q` → 31 passed.

- [ ] **Step 4: Commit**

```bash
git add audit/audit_engine.py
git commit -m "feat: drop subdomain pages from deep crawl; AI promoter-vs-incidental check"
```

## Escalation
If the `pg_ranking_urls` construction or the porn/gamble decision block differs from the description, STOP and report NEEDS_CONTEXT with the actual code.

---

## Task 4: Docs + regression

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Full regression** — `.venv/Scripts/python.exe -m pytest -q`. Expect all green. If anything fails, STOP and report BLOCKED.

- [ ] **Step 2: Document** — read `README.md`, then add a concise note to the porn/gambling section: subdomain pages are excluded from the deep crawl (Google treats subdomains as separate sites); when a site would be skipped for gambling links, an AI check distinguishes a gambling **promoter** (Skip) from a neutral directory/news/B2B **incidental** linker (Check manually); and the content-farm check no longer flags a business's service/product pages as trivia. Keep it brief.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: subdomain exclusion + promoter/incidental gambling check"
```

---

## Self-Review Notes (for the implementer)

- **Order in audit_domain:** subdomain filter (Step 1) runs at the deep-crawl page-list build (so china.xavor.com is never crawled → its links never reach the count); the promoter check (Step 2) runs only when the count would SKIP.
- **`source_pages`** = deep pages that hold bad links (clodura's `/directory/company/...`) + the homepage if it held any. Cap is handled inside `classify_gambling_link_context` (15).
- **AI-error default** is `"incidental"` → manual review, never a false auto-skip.
- **Expected:** clodura → CHECK_MANUALLY (incidental directory); xavor → 0 gambling after subdomain filter → continue/Approved; zazz → PBN/Spam rows now say "porn/gamble check sent to manual review"; vrinsofts service pages no longer trivia.

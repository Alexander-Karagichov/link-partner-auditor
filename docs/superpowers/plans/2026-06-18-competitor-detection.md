# Competitor Detection vs. Your Business — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Flag audited sites that compete with the user's own business — definite via their `competitor_sites.txt` list, discovered via the AI comparing each site's niche to the user's (sourced from `data/my_business.txt`), replacing the hardcoded "Bright Data".

**Architecture:** A pure `is_listed_competitor` helper; a cached `get_my_business()` that scrapes the user's homepage once for their niche; the existing `analyze_audit` prompt parameterized with `my_business`; `audit_domain` combines list-OR-AI into `result.is_competitor` and a flag; UI reads the new field.

**Tech Stack:** Python 3, Streamlit, pytest.

**Spec:** `docs/superpowers/specs/2026-06-18-competitor-detection-design.md`

**Conventions:** run tests with `.venv/Scripts/python.exe -m pytest ...`; use the **Bash** tool; do NOT pip install.

---

## Task 1: `is_listed_competitor` (link_checker_service)

**Files:**
- Modify: `services/link_checker_service.py`
- Test: `tests/test_is_listed_competitor.py`

`_load_competitor_domains()` (cached in `_COMPETITOR_DOMAINS`) and `_is_match(host, base)` already exist.

- [ ] **Step 1: Write failing test** — create `tests/test_is_listed_competitor.py`:

```python
from services import link_checker_service as lc


def test_is_listed_competitor(monkeypatch):
    monkeypatch.setattr(lc, "_COMPETITOR_DOMAINS", ["rival.com", "foo.io"])
    assert lc.is_listed_competitor("rival.com") is True
    assert lc.is_listed_competitor("www.rival.com") is True
    assert lc.is_listed_competitor("blog.rival.com") is True
    assert lc.is_listed_competitor("notrival.com") is False
    assert lc.is_listed_competitor("") is False
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_is_listed_competitor.py -v`
Expected: FAIL — `is_listed_competitor` not defined.

- [ ] **Step 3: Implement** — add to `services/link_checker_service.py` (after `check_competitor_links`):

```python
def is_listed_competitor(domain: str) -> bool:
    """True if `domain` is (or is a subdomain of) an entry in competitor_sites.txt."""
    d = (domain or "").lower().removeprefix("www.")
    if not d:
        return False
    return any(_is_match(d, comp) for comp in _load_competitor_domains())
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_is_listed_competitor.py -v`
Expected: 1 passed. Then full suite (expect 32).

- [ ] **Step 5: Commit**

```bash
git add services/link_checker_service.py tests/test_is_listed_competitor.py
git commit -m "feat: is_listed_competitor helper"
```

---

## Task 2: `my_business.txt` + config

**Files:**
- Create: `data/my_business.txt`
- Modify: `config/settings.py`

- [ ] **Step 1: Create `data/my_business.txt`:**

```
# Your own domain — used to detect whether an audited site is YOUR competitor.
# One domain, no scheme. The tool scrapes its homepage once to learn your niche.
brightdata.com
```

- [ ] **Step 2: Add the setting** — in `config/settings.py`, near `COMPETITOR_SITES_FILE`, add:

```python
MY_BUSINESS_FILE: Path = DATA_DIR / "my_business.txt"
```

- [ ] **Step 3: Verify**

Run: `.venv/Scripts/python.exe -c "from config import settings as s; print(s.MY_BUSINESS_FILE.name, s.MY_BUSINESS_FILE.exists())"`
Expected: `my_business.txt True`. Then full suite green.

- [ ] **Step 4: Commit**

```bash
git add data/my_business.txt config/settings.py
git commit -m "feat: my_business.txt config (your own domain)"
```

---

## Task 3: Parameterize the analysis prompt (llm_service)

**Files:**
- Modify: `services/llm_service.py`

- [ ] **Step 1: Add `my_business` to `_build_analyze_prompt`.** Change its signature:

```python
def _build_analyze_prompt(audit_data: dict, homepage_text: str = "", about_page_text: str = "",
                          my_business: Optional[dict] = None) -> str:
```
(`Optional` is imported in this module — confirm; if not, add `from typing import Optional`.)

At the start of the function body (after `domain = audit_data.get("domain", "unknown")`), add:

```python
    my_business = my_business or {}
    _md = my_business.get("domain") or "your business"
    _mn = my_business.get("niche") or ""
    _md_ctx = f"{_md} ({_mn})" if _mn else _md
```

- [ ] **Step 2: Replace the four hardcoded "Bright Data" lines** in the `sections` list:

```python
        f"You are a senior SEO and brand-safety analyst at Bright Data.",
```
→
```python
        f"You are a senior SEO and brand-safety analyst evaluating link partners for {_md_ctx}.",
```

```python
        "  - `recommendation`: 1-3 sentence recommendation for the Bright Data team",
        "  - `competitor_risk`: boolean – does this domain appear to directly compete with Bright Data?",
        "  - `brand_safe`: boolean – is this domain safe to associate with Bright Data?",
```
→
```python
        f"  - `recommendation`: 1-3 sentence recommendation for the {_md} team",
        f"  - `competitor_risk`: boolean – does this domain DIRECTLY compete with {_md_ctx}? Judge by whether it is in the same or a directly-overlapping business/niche (not merely adjacent).",
        "  - `competitor_reason`: short reason (<=12 words) if competitor_risk is true, else empty string",
        f"  - `brand_safe`: boolean – is this domain safe to associate with {_md}?",
```

- [ ] **Step 2b: Add `my_business` to `analyze_audit`.** Change its signature:

```python
def analyze_audit(audit_data: dict, about_page_text: str = "", homepage_text: str = "",
                  my_business: Optional[dict] = None) -> dict:
```
Add `"competitor_reason": "",` to `default_response`. And pass it through:

```python
        user = _build_analyze_prompt(audit_data, homepage_text=homepage_text,
                                     about_page_text=about_page_text, my_business=my_business)
```

- [ ] **Step 3: Verify**

Run: `.venv/Scripts/python.exe -c "from services import llm_service as m; print(m._build_analyze_prompt({'domain':'x'}, my_business={'domain':'acme.com','niche':'CRM'}).count('Bright Data'))"`
Expected: `0` (no Bright Data when my_business is set).
Then `.venv/Scripts/python.exe -m pytest -q` → 32 passed.

- [ ] **Step 4: Commit**

```bash
git add services/llm_service.py
git commit -m "feat: parameterize analysis prompt with the user's own business"
```

---

## Task 4: audit_engine — get_my_business, fields, combine, flag

**Files:**
- Modify: `audit/audit_engine.py`

- [ ] **Step 1: Add `get_my_business()` (cached).** Near the other cached loaders (after `get_linkbuilding_targets`), add:

```python
_my_business: Optional[dict] = None


def get_my_business() -> dict:
    """{"domain","niche"} for the user's own site. Domain from my_business.txt (fallback
    LINKBUILDING_TARGET_DOMAIN); niche derived by scraping the homepage once. Cached."""
    global _my_business
    if _my_business is not None:
        return _my_business
    domain = ""
    try:
        path = settings.MY_BUSINESS_FILE
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip().lower()
                if line and not line.startswith("#"):
                    domain = (line.removeprefix("https://").removeprefix("http://")
                              .removeprefix("www.").split("/")[0])
                    break
    except Exception as exc:
        logger.warning("Could not read my_business file: %s", exc)
    if not domain:
        domain = settings.LINKBUILDING_TARGET_DOMAIN
    niche = ""
    try:
        if domain:
            _html, _err = bdata.scrape_page(f"https://{domain}")
            if _html:
                niche = ai_service.determine_niche(link_checker.extract_page_text(_html))
    except Exception as exc:
        logger.warning("Could not derive my-business niche for %s: %s", domain, exc)
    _my_business = {"domain": domain, "niche": niche}
    logger.info("My business: %s (niche: %s)", domain, niche or "unknown")
    return _my_business
```

- [ ] **Step 2: Reset it in `reload_keywords`.** Add `_my_business` to the `global` line and set `_my_business = None` (alongside the other resets).

- [ ] **Step 3: Add `AuditResult` fields.** After `niche: str = ""` (or near the AI-analysis fields), add:

```python
    is_competitor: bool = False
    competitor_reason: str = ""
```
And in `to_dict`, after the `"niche": self.niche,` entry add:
```python
            "is_competitor": self.is_competitor,
            "competitor_reason": self.competitor_reason,
```

- [ ] **Step 4: Pass my_business into analyze_audit + combine the verdict.** Find the AI analysis block:
```python
    _audit_dict = result.to_dict()
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_analysis = pool.submit(
            ai_service.analyze_audit, _audit_dict,
            result.about_page_text or "", result.homepage_text or "",
        )
```
Change to compute my_business first and pass it:
```python
    _audit_dict = result.to_dict()
    _my_biz = get_my_business()
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_analysis = pool.submit(
            ai_service.analyze_audit, _audit_dict,
            result.about_page_text or "", result.homepage_text or "", _my_biz,
        )
```
Then, right after `result.ai_analysis = analysis`, add the combine:
```python
    _listed_comp = link_checker.is_listed_competitor(domain)
    result.is_competitor = _listed_comp or bool(result.ai_analysis.get("competitor_risk"))
    if _listed_comp:
        result.competitor_reason = "On your competitor list"
    elif result.is_competitor:
        result.competitor_reason = result.ai_analysis.get("competitor_reason", "") or "Same/overlapping business"
```

- [ ] **Step 5: Add the competitor flag in the headline builder.** Find `_flags = rec.collect_flags(...)` in the headline builder; immediately AFTER that call (before the `if _pg_manual:` block), add:

```python
    if result.is_competitor:
        _flags = _flags + [f"Competitor — {result.competitor_reason}"]
```

- [ ] **Step 6: Verify**

1. `.venv/Scripts/python.exe -c "import audit.audit_engine as a; r=a.AuditResult(domain='x',input_url='http://x'); d=r.to_dict(); print(d['is_competitor'], repr(d['competitor_reason']))"` → `False ''`
2. `.venv/Scripts/python.exe -c "import audit.audit_engine; print('ok')"` → `ok`
3. `grep -n "get_my_business\|is_competitor\|is_listed_competitor" audit/audit_engine.py` → shows the wiring.
4. `.venv/Scripts/python.exe -m pytest -q` → 32 passed.

- [ ] **Step 7: Commit**

```bash
git add audit/audit_engine.py
git commit -m "feat: get_my_business + competitor verdict (list OR AI) + flag"
```

## Escalation
If the AI-analysis block, `reload_keywords`, or the headline builder differs from the description, STOP and report NEEDS_CONTEXT.

---

## Task 5: UI — read `is_competitor`

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Summary column.** Find:
```python
            "Is a Competitor?": "Yes" if r.ai_analysis.get("competitor_risk") else "No",
```
Replace with:
```python
            "Is a Competitor?": "Yes" if getattr(r, "is_competitor", False) else "No",
```

- [ ] **Step 2: AI-banner competitor line.** Find the per-result detail line that sets `comp_risk = result.ai_analysis.get("competitor_risk")` and renders "Is a Competitor: ⚠️ Yes / ✅ No". Change the source to `result.is_competitor` and append the reason when present:
```python
            comp_risk = getattr(result, "is_competitor", False)
```
and where it renders "Is a Competitor: ⚠️ Yes", append the reason, e.g.:
```python
                if comp_risk:
                    st.markdown(f"**Is a Competitor: ⚠️ Yes**")
                    if getattr(result, "competitor_reason", ""):
                        st.caption(result.competitor_reason)
                else:
                    st.markdown("**Is a Competitor: ✅ No**")
```
(Read the actual block and adapt the markdown lines; keep the existing layout.)

- [ ] **Step 3: Verify**

1. `.venv/Scripts/python.exe -m py_compile app.py` → no error.
2. `.venv/Scripts/python.exe -c "import ast; ast.parse(open('app.py',encoding='utf-8').read()); print('parse ok')"` → `parse ok`.
3. `grep -n "competitor_risk" app.py` → no output (both reads now use is_competitor).
4. Full suite green. Do NOT launch streamlit.

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "ui: competitor column/banner read is_competitor"
```

## Escalation
If the competitor banner block isn't structured as described, STOP and report NEEDS_CONTEXT.

---

## Task 6: Docs + regression

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Full regression** — `.venv/Scripts/python.exe -m pytest -q`. Expect 32 passed. If anything fails, STOP and report BLOCKED.

- [ ] **Step 2: Document** — read `README.md`, then add a concise note + the new file row: put your domain in **`data/my_business.txt`**; the tool flags audited sites that compete with you — definite if on your `competitor_sites.txt` list, otherwise the AI compares the site's niche to yours (derived by scraping your homepage once). Informational (shown in the "Is a Competitor?" column + a flag); doesn't change the verdict. Add a `data/my_business.txt` row to the data-files table.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: competitor detection vs your own business"
```

---

## Self-Review Notes (for the implementer)

- **`get_my_business` is cached** (scrapes your homepage once per process; reset on Reload). For a bulk run, the first domain pays the scrape; the rest reuse it.
- **Combine = list OR AI.** A listed competitor reads "On your competitor list"; an AI-only match uses the AI's `competitor_reason`.
- **Flag, not gate:** the competitor flag is appended in the headline builder (non-skipped paths). It never changes the Skip/Manual/Approved decision.
- **`Optional`** is already imported in both `llm_service.py` and `audit_engine.py`.

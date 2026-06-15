# Phase Panel, Early Niche & Spam/PBN Rubric Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add early niche detection, a clear per-phase results panel (with skip reasons), and expandable PBN/Spam rubrics — and remove the now-redundant standalone banners.

**Architecture:** A short `determine_niche` LLM call runs after homepage processing and stores `result.niche`. A pure, tested `build_phase_rows` helper drives a vertical phase panel in the Streamlit UI; PBN/Spam phases get expandable "why" panels reading existing fields. Three redundant banners are removed.

**Tech Stack:** Python 3, Streamlit, pytest (already set up). LLM via `_backend.chat_json` + `_parse_json`.

**Spec:** `docs/superpowers/specs/2026-06-15-results-display-niche-design.md`

**Conventions (verified):** run tests with `.venv/Scripts/python.exe -m pytest ...`; use the **Bash** tool; do NOT pip install. LLM helpers in `services/llm_service.py`: `_backend.chat_json(system, prompt, max_tokens=...)`, `_parse_json(raw)` (returns dict), `logger`, `json`. `audit_engine` alias `ai_service = llm_service`.

---

## Task 1: `determine_niche` LLM function

**Files:**
- Modify: `services/llm_service.py` (add after `assess_content_farm` or near the other classifiers)

- [ ] **Step 1: Implement** — add to `services/llm_service.py`:

```python
def determine_niche(homepage_text: str, about_text: str = "") -> str:
    """Return a 3-6 word niche/topic/industry description of the site, or "" on empty/error."""
    if not homepage_text:
        return ""
    try:
        ctx = homepage_text[:2000]
        if about_text:
            ctx += "\n\nAbout page:\n" + about_text[:1000]
        prompt_parts = [
            "Identify the website's primary niche / topic / industry from the text below.",
            "", "## Site text", ctx, "",
            'Return ONLY JSON: {"niche": "<3-6 word description>"}',
        ]
        system = "You are an SEO analyst. You always respond with valid JSON only."
        raw = _backend.chat_json(system, "\n".join(prompt_parts), max_tokens=60)
        parsed = _parse_json(raw)
        return str(parsed.get("niche", "")).strip()
    except Exception:
        logger.exception("determine_niche failed")
        return ""
```

- [ ] **Step 2: Verify import**

Run: `.venv/Scripts/python.exe -c "from services import llm_service as m; print(hasattr(m,'determine_niche'))"`
Expected: `True`. Then `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 3: Commit**

```bash
git add services/llm_service.py
git commit -m "feat: determine_niche LLM helper"
```

---

## Task 2: `AuditResult.niche` field + audit_domain wiring

**Files:**
- Modify: `audit/audit_engine.py` (`AuditResult` dataclass + `to_dict` + `audit_domain`)

- [ ] **Step 1: Add the field** — in `AuditResult`, after the `recommendation` field, add:

```python
    niche: str = ""   # 3-6 word niche/topic, determined right after the homepage gate passes
```

- [ ] **Step 2: Add to `to_dict`** — after the `"recommendation_reason": ...` entry, add:

```python
            "niche": self.niche,
```

- [ ] **Step 3: Wire the niche call in `audit_domain`.** Read `audit_domain` and find the `# ── Decision: data sufficiency` block (returns CHECK_MANUALLY when `not html`) and the `# ── SERP results (Bright Data Google site: checks)` block right after it. Insert BETWEEN them (after the data-sufficiency block, before the SERP-results block) — at this point `html` is truthy and `result.homepage_text`/`result.about_page_text` are set:

```python
    # ── Niche (informational; determined once the homepage gate passed) ────────
    if result.homepage_text:
        result.niche = ai_service.determine_niche(result.homepage_text, result.about_page_text or "")
        logger.info("[%s] Niche: %s", domain, result.niche or "(unknown)")
```

- [ ] **Step 4: Verify**

Run: `.venv/Scripts/python.exe -c "from audit.audit_engine import AuditResult; r=AuditResult(domain='x', input_url='http://x'); print(repr(r.niche), r.to_dict()['niche'])"`
Expected: `'' ''`
Run: `.venv/Scripts/python.exe -c "import audit.audit_engine; print('ok')"` → `ok`. Then `.venv/Scripts/python.exe -m pytest -q` → green.

- [ ] **Step 5: Commit**

```bash
git add audit/audit_engine.py
git commit -m "feat: AuditResult.niche + early niche detection in audit_domain"
```

---

## Task 3: `build_phase_rows` pure helper

**Files:**
- Modify: `services/recommendation_service.py` (add function)
- Test: `tests/test_phase_rows.py`

- [ ] **Step 1: Write failing tests** — create `tests/test_phase_rows.py`:

```python
from services import recommendation_service as rec


def test_all_phases_ran_approved():
    steps = {
        "homepage_gate": {"status": "PASS", "detail": ""},
        "porn_gamble_links": {"status": "PASS", "count": 0, "examples": []},
        "pbn": {"status": "PASS", "band": "MEDIUM", "score": 45},
        "content_farm": {"status": "PASS", "band": "LOW", "score": 10, "semrush_checked": False},
    }
    rows = rec.build_phase_rows(steps, "Marketing SaaS")
    assert [r["name"] for r in rows] == ["Homepage gate", "Niche", "P/G links", "PBN", "Spam / content farm"]
    assert rows[1]["status"] == "INFO" and rows[1]["detail"] == "Marketing SaaS"
    pbn = next(r for r in rows if r["name"] == "PBN")
    assert pbn["status"] == "MEDIUM" and "45" in pbn["detail"]


def test_skip_at_pg():
    steps = {
        "homepage_gate": {"status": "PASS", "detail": ""},
        "porn_gamble_links": {"status": "FAIL", "count": 32, "examples": []},
    }
    rows = rec.build_phase_rows(steps, "Tech news")
    pg = next(r for r in rows if r["name"] == "P/G links")
    assert pg["status"] == "FAIL" and "32" in pg["detail"]
    for name in ("PBN", "Spam / content farm"):
        row = next(r for r in rows if r["name"] == name)
        assert row["status"] == "SKIPPED" and "P/G links" in row["detail"]


def test_skip_at_homepage():
    rows = rec.build_phase_rows({"homepage_gate": {"status": "FAIL", "detail": "links to casino"}}, "")
    assert rows[1]["detail"] == "—"   # empty niche
    for name in ("P/G links", "PBN", "Spam / content farm"):
        row = next(r for r in rows if r["name"] == name)
        assert row["status"] == "SKIPPED" and "homepage" in row["detail"]
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase_rows.py -v`
Expected: FAIL — `build_phase_rows` not defined.

- [ ] **Step 3: Implement** — add to `services/recommendation_service.py`:

```python
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
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_phase_rows.py -v`
Expected: 3 passed. Then full suite green.

- [ ] **Step 5: Commit**

```bash
git add services/recommendation_service.py tests/test_phase_rows.py
git commit -m "feat: build_phase_rows for the results phase panel"
```

---

## Task 4: UI — phase panel, rubric expanders, remove banners

**Files:**
- Modify: `app.py` (`render_domain_detail` + the summary-row `Niche` column)

Read `render_domain_detail` (~lines 318–407) first. Current structure: verdict banner + flags (325–332) → scorecard block (333–341) → AI Analysis banner (343–366, includes a `**Risk: …**` line ~352 and a niche caption ~353–354) → PBN banner (368–390) → Content-farm banner (392–407). `import services.recommendation_service as rec` may need adding at the top of `app.py` if not present — check imports.

- [ ] **Step 1: Ensure the helper is importable.** At the top of `app.py` with the other imports, ensure there is `from services import recommendation_service as rec` (add it if absent).

- [ ] **Step 2: Replace the scorecard block (the `_steps`/`_sc` lines through `st.divider()`) with the phase panel + rubric expanders.** Replace exactly this block:

```python
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

with:

```python
            _steps = _reco.get("steps") or {}
            _rows = rec.build_phase_rows(_steps, getattr(result, "niche", "") or "")
            _ph_emoji = {"PASS": "🟢", "FAIL": "🔴", "SKIPPED": "➖", "INFO": "🏷️",
                         "LOW": "🟢", "MEDIUM": "🟠", "HIGH": "🔴"}
            st.markdown("**Phases**")
            for _row in _rows:
                _e = _ph_emoji.get(_row["status"], "•")
                if _row["status"] == "INFO":
                    st.markdown(f"{_e} **{_row['name']}**: {_row['detail']}")
                else:
                    _d = f" — {_row['detail']}" if _row.get("detail") else ""
                    st.markdown(f"{_e} **{_row['name']}**: {_row['status']}{_d}")
            if result.pbn:
                with st.expander("Why — PBN score"):
                    st.markdown(f"**{result.pbn.get('pbn_risk', 'UNKNOWN')}** · score {result.pbn.get('pbn_score', 0)}/100")
                    for _rsn in (result.pbn.get("reasons") or []):
                        st.markdown(f"- {_rsn}")
                    _sig = result.pbn.get("signals") or {}
                    if _sig:
                        st.caption("Signals: " + ", ".join(f"{k}={v}" for k, v in _sig.items()))
                    st.caption("Bands: LOW <20 · MEDIUM 20–44 · HIGH 45+")
            cfarm = getattr(result, "content_farm", None) or {}
            if cfarm:
                with st.expander("Why — Spam / content-farm score"):
                    st.markdown(f"**{cfarm.get('band', 'UNKNOWN')}** · score {cfarm.get('score', 0)}/100"
                                + ("" if cfarm.get("semrush_checked") else " · SEMrush skipped"))
                    for _rsn in (cfarm.get("reasons") or []):
                        st.markdown(f"- {_rsn}")
                    _sig = cfarm.get("signals") or {}
                    if _sig:
                        st.caption("Signals: " + ", ".join(f"{k}={v}" for k, v in _sig.items()))
                    if cfarm.get("trash_examples"):
                        st.caption("Sample trash: " + ", ".join(cfarm["trash_examples"][:3]))
                    st.caption("Bands: LOW <25 · MEDIUM 25–54 · HIGH 55+")
            st.divider()
```

- [ ] **Step 3: Remove the standalone `**Risk: …**` line** in the AI Analysis banner. Delete exactly:

```python
                st.markdown(f"**Risk: {emoji} {risk}**")
```

- [ ] **Step 4: Make the AI niche caption prefer `result.niche`.** Replace:

```python
                if result.ai_analysis.get("website_niche"):
                    st.caption(f"🏷️ {result.ai_analysis["website_niche"]}")
```
with:

```python
                _niche = getattr(result, "niche", "") or result.ai_analysis.get("website_niche", "")
                if _niche:
                    st.caption(f"🏷️ {_niche}")
```

- [ ] **Step 5: Remove the standalone PBN banner block** — delete the entire `# ── PBN / link-network banner` block (from that comment through the `st.caption(" · ".join(_meta))` line, i.e. the whole `if result.pbn and result.pbn.get("pbn_risk"):` block).

- [ ] **Step 6: Remove the standalone Content-farm banner block** — delete the entire `# ── Content-farm banner` block (from that comment through the `st.caption("Sample trash articles: ...")` line, i.e. the whole `if cfarm.get("content_farm_risk"):` block).

- [ ] **Step 7: Summary table Niche column prefers `result.niche`.** Find the row dict entry `"Niche": r.ai_analysis.get("website_niche", "") if r.ai_analysis else ""` and replace with:

```python
            "Niche": (getattr(r, "niche", "") or (r.ai_analysis.get("website_niche", "") if r.ai_analysis else "")),
```

- [ ] **Step 8: Verify**

1. `.venv/Scripts/python.exe -m py_compile app.py` → no error.
2. `.venv/Scripts/python.exe -c "import ast; ast.parse(open('app.py',encoding='utf-8').read()); print('parse ok')"` → `parse ok`.
3. `.venv/Scripts/python.exe -c "import app; print('import ok')"` → `import ok` (Streamlit bare-mode warnings OK).
4. Confirm the banners are gone: `grep -n "PBN / link-network banner\|Content-farm banner\|Content-Farm Risk\|Risk: {emoji}" app.py` → no output.
5. Full suite: `.venv/Scripts/python.exe -m pytest -q` → green. Do NOT launch streamlit.

- [ ] **Step 9: Commit**

```bash
git add app.py
git commit -m "feat: results phase panel + PBN/Spam rubric expanders; remove redundant banners"
```

## Escalation
If the block boundaries differ from the description (e.g. the banners were already removed or reshaped), STOP and report NEEDS_CONTEXT with the actual code.

---

## Task 5: Docs + full regression

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Full regression** — `.venv/Scripts/python.exe -m pytest -q`. Expect all green. If anything fails, STOP and report BLOCKED.

- [ ] **Step 2: Document** — read `README.md`, then add to the recommendation section a short note: the detailed results now show a **per-phase panel** (Homepage gate, Niche, P/G links, PBN, Spam) with skipped phases labelled by reason; PBN and Spam have an expandable rubric (reasons + signals + band thresholds); the site **niche** is determined right after the homepage gate (so it shows even on SKIP). Keep it concise.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: phase panel, niche, and rubric in results"
```

---

## Self-Review Notes (for the implementer)

- **Task 4 is the bulk** — it both adds (phase panel + expanders) and removes (3 banners + Risk line). Use the exact block text from the file for the deletions; the expander content for PBN/Spam mirrors the removed banners, so nothing is lost, just relocated into expanders.
- **`build_phase_rows`** is pure and fully tested; the UI only renders it. Skip reasons come from the helper, derived from which steps are present.
- **Niche placement** must be after the data-sufficiency early-return (so `html` is truthy) and before the deep crawl/Step-3, so a later porn/gamble SKIP still carries the niche.
- **`result.pbn` / `result.content_farm` empty on SKIP** → the expanders are guarded by `if result.pbn:` / `if cfarm:`, so they don't render on short-circuited results (the phase rows still show them SKIPPED).

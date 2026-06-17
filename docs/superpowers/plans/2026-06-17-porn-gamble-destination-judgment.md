# Destination-Based Porn/Gamble Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop flagging legit sites that merely write about porn/gambling — judge the destination DOMAIN's nature, drop the naive keyword heuristic, and make the verdict count-based (3+ distinct confirmed sites → Skip, 1–2 → Check manually, 0 → continue).

**Architecture:** The AI classifiers judge bare domains with a sharpened prompt. The keyword word-match is removed from both the decision and the UI. A pure helper computes the distinct confirmed porn/gambling domain set and the count-based decision; `audit_domain` uses it (gate no longer instant-skips on one link; keeps a ≥threshold fast-path).

**Tech Stack:** Python 3, Streamlit, pytest. LLM via `_backend.chat_json` + `_parse_json`.

**Spec:** `docs/superpowers/specs/2026-06-17-porn-gamble-destination-judgment-design.md`

**Conventions:** run tests with `.venv/Scripts/python.exe -m pytest ...`; use the **Bash** tool; do NOT pip install.

---

## File Structure

**Modify:**
- `services/llm_service.py` — `classify_outbound_links` → domain-level + sharper prompt; sharpen `classify_link_partners` prompt; add `_domain_of`.
- `services/recommendation_service.py` — add `confirmed_pg_domains`, `decide_porn_gamble`, `_domain_of`.
- `config/settings.py` — `PORN_GAMBLE_SKIP_THRESHOLD`.
- `audit/audit_engine.py` — gate rework, homepage merge, drop keyword heuristic, count-based decision.
- `app.py` — remove keyword "external gambling/adult link(s)" displays; add WARN emoji.

**Test:**
- `tests/test_pg_decision.py` (new).

---

## Task 1: AI judges the bare domain (llm_service)

**Files:**
- Modify: `services/llm_service.py`

- [ ] **Step 1: Add a domain helper + import.** Near the top of `services/llm_service.py` (with the other imports) add `from urllib.parse import urlparse`. Add this helper after the imports:

```python
def _domain_of(href: str) -> str:
    """Registrable host of an href or a bare domain string ('bodog.eu' or full URL)."""
    href = (href or "").strip()
    if not href:
        return ""
    parsed = urlparse(href if "//" in href else "//" + href)
    return (parsed.netloc or "").lower().removeprefix("www.")
```

- [ ] **Step 2: Replace `classify_outbound_links`** with a domain-judging version (keeps the `{found_href, category, reason}` return shape so the deep-crawl merge by `found_href` still works):

```python
def classify_outbound_links(page_url: str, external_links: list[str]) -> list[dict]:
    """
    Classify external links by the NATURE OF THEIR DESTINATION DOMAIN (not the
    URL's topic). Returns {found_href, category, reason} for every link whose
    DOMAIN is a gambling/adult operator. A site that merely writes ABOUT these
    topics (news/academic/medical/security/safety) is NOT flagged.
    """
    if not external_links:
        return []
    default_response: list[dict] = []
    try:
        url_domain = {href: _domain_of(href) for href in external_links}
        domains = sorted({d for d in url_domain.values() if d})
        if not domains:
            return []
        domains_block = "\n".join(f"- {d}" for d in domains[:60])
        prompt_parts = [
            f"You are a brand-safety analyst reviewing the OUTBOUND-LINK DESTINATIONS of this page: {page_url}",
            "",
            "Below are the destination DOMAINS linked from the page body.",
            "",
            "## Destination domains",
            domains_block,
            "",
            "## Task",
            "For EACH domain, decide whether the SITE ITSELF is a gambling, social-casino, "
            "sports-betting, adult/porn, or escort operator.",
            "IMPORTANT: Judge the destination SITE, not the topic of any URL or article. A news, "
            "academic, medical, security, government, or online-safety site that merely WRITES "
            "ABOUT gambling or porn is NOT a gambling/adult site — do NOT flag it. Only flag a "
            "domain that IS such an operator. When unsure, do NOT flag.",
            "",
            "Return a JSON object with a single key `flagged_domains`: an array of objects with "
            "`domain`, `category` (gambling|social_casino|sports_betting|adult|escort|other_harmful), "
            "and a one-sentence `reason`. If none, return {\"flagged_domains\": []}. "
            "Return ONLY the JSON object.",
        ]
        system = "You are a brand-safety content classifier. You always respond with valid JSON only."
        raw = _backend.chat_json(system, "\n".join(prompt_parts), max_tokens=600)
        parsed = _parse_json(raw)
        flagged: dict[str, dict] = {}
        for it in parsed.get("flagged_domains", []):
            d = str(it.get("domain", "")).lower().removeprefix("www.")
            if d:
                flagged[d] = {"category": str(it.get("category", "gambling")), "reason": str(it.get("reason", ""))}
        out: list[dict] = []
        for href, d in url_domain.items():
            if d in flagged:
                out.append({"found_href": href, "category": flagged[d]["category"], "reason": flagged[d]["reason"]})
        return out
    except Exception:
        logger.exception("Unexpected error classifying outbound links")
        return default_response
```

- [ ] **Step 3: Sharpen `classify_link_partners`.** In that function's prompt (the part listing the three categories), insert this guidance line just before the "Return a JSON object" line:

```python
            "IMPORTANT: judge the destination SITE itself, not the topic of any URL or anchor. "
            "A news/academic/medical/security/online-safety site that merely writes ABOUT gambling "
            "or porn is NOT 'gambling_porn' — classify it 'legit'. Only use 'gambling_porn' for a "
            "domain that IS a gambling operator or an adult/porn site.",
```

- [ ] **Step 4: Verify**

Run: `.venv/Scripts/python.exe -c "from services import llm_service as m; print(m._domain_of('https://www.webroot.com/us/en/x'), '|', m._domain_of('bodog.eu'))"`
Expected: `webroot.com | bodog.eu`
Run: `.venv/Scripts/python.exe -m pytest -q` → all green (26).

- [ ] **Step 5: Commit**

```bash
git add services/llm_service.py
git commit -m "feat: AI judges destination domain (not URL topic) for gambling/porn"
```

---

## Task 2: Count helper + decision (recommendation_service)

**Files:**
- Modify: `services/recommendation_service.py`
- Test: `tests/test_pg_decision.py`

- [ ] **Step 1: Write failing tests** — create `tests/test_pg_decision.py`:

```python
from services import recommendation_service as rec


def test_confirmed_pg_domains_dedup():
    hp = [{"found_href": "https://bodog.eu/poker"}]
    deep = [{"bad_links": [{"found_href": "https://www.bodog.eu/casino"}, {"found_href": "gamblizard.com"}]}]
    assert rec.confirmed_pg_domains(hp, deep) == ["bodog.eu", "gamblizard.com"]


def test_confirmed_pg_domains_empty():
    assert rec.confirmed_pg_domains([], []) == []


def test_decide_skip_manual_continue():
    assert rec.decide_porn_gamble(["a.com", "b.com", "c.com"], 3)[0] == "SKIP"
    d, reason, flag = rec.decide_porn_gamble(["a.com", "b.com"], 3)
    assert d == "CHECK_MANUALLY" and "a.com" in flag and "2" in reason
    assert rec.decide_porn_gamble([], 3) == (None, "", None)
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pg_decision.py -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement** — add to `services/recommendation_service.py` (add `from urllib.parse import urlparse` at the top if absent):

```python
def _domain_of(href: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    parsed = urlparse(href if "//" in href else "//" + href)
    return (parsed.netloc or "").lower().removeprefix("www.")


def confirmed_pg_domains(homepage_bad_links: list, deep_page_checks: list) -> list[str]:
    """Distinct registrable domains from known-bad + AI-flagged links (homepage + deep)."""
    seen: set[str] = set()
    out: list[str] = []
    def _add(href: str) -> None:
        d = _domain_of(href)
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    for b in (homepage_bad_links or []):
        _add(b.get("found_href", ""))
    for c in (deep_page_checks or []):
        for b in c.get("bad_links", []):
            _add(b.get("found_href", ""))
    return out


def decide_porn_gamble(pg_domains: list[str], threshold: int) -> tuple:
    """
    Return (decision, reason, flag). decision ∈ 'SKIP' | 'CHECK_MANUALLY' | None.
    >= threshold distinct domains → SKIP; 1..threshold-1 → CHECK_MANUALLY; 0 → None.
    """
    n = len(pg_domains)
    if n >= threshold:
        return "SKIP", f"Linking to {n} porn/gamble sites", None
    if n >= 1:
        listed = ", ".join(pg_domains[:5])
        return "CHECK_MANUALLY", f"Links to {n} porn/gamble site(s)", f"Links to {n} porn/gamble site(s): {listed}"
    return None, "", None
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pg_decision.py -v`
Expected: 3 passed. Then full suite (expect 29).

- [ ] **Step 5: Commit**

```bash
git add services/recommendation_service.py tests/test_pg_decision.py
git commit -m "feat: confirmed_pg_domains + count-based porn/gamble decision"
```

---

## Task 3: Config threshold

**Files:**
- Modify: `config/settings.py`

- [ ] **Step 1: Add setting** — after the recommendation config block (`RECO_*`), add:

```python
# Distinct confirmed porn/gambling DESTINATION sites a partner may link to before
# it is auto-skipped. Below this (but >0) → Check manually. Counts distinct domains.
PORN_GAMBLE_SKIP_THRESHOLD: int = int(os.getenv("PORN_GAMBLE_SKIP_THRESHOLD", "3"))
```

- [ ] **Step 2: Verify**

Run: `.venv/Scripts/python.exe -c "from config import settings as s; print(s.PORN_GAMBLE_SKIP_THRESHOLD)"`
Expected: `3`. Then full suite green.

- [ ] **Step 3: Commit**

```bash
git add config/settings.py
git commit -m "feat: PORN_GAMBLE_SKIP_THRESHOLD config"
```

---

## Task 4: Rework audit_domain (gate + count decision) — the big one

**Files:**
- Modify: `audit/audit_engine.py`

Read `audit_domain` and `_homepage_gambling_gate` fully first. Make these changes:

- [ ] **Step 1: Rework `_homepage_gambling_gate`.** Replace the whole function so it (a) drops the keyword "1b" block entirely, and (b) no longer instant-fails — it returns the homepage offending links + buckets + verdicts:

```python
def _homepage_gambling_gate(domain: str, html: str) -> tuple[list[dict], dict, list[dict]]:
    """
    Find homepage outbound links to gambling/porn: known_bad_sites.txt matches +
    AI domain classification. Returns (offending, buckets, verdicts). The CALLER
    counts distinct domains to decide Skip/Check-manually (no instant fail here).
    """
    from services import outbound_classifier as oc
    offending: list[dict] = []
    link_result = link_checker.check_links(html, f"https://{domain}")
    for m in link_result.bad_link_matches:
        offending.append({"found_href": m.found_href, "matched_bad_domain": m.matched_bad_domain, "link_text": m.link_text})
    buckets = oc.classify_outbound(html, f"https://{domain}")
    candidates = buckets.get("candidates", [])
    verdicts = ai_service.classify_link_partners(f"https://{domain}", candidates) if candidates else []
    for v in verdicts:
        if v.get("category") == "gambling_porn":
            offending.append({"found_href": v["domain"], "matched_bad_domain": f"[AI: {v.get('reason', 'gambling/adult')}]", "link_text": ""})
    return offending, buckets, verdicts
```

- [ ] **Step 2: Rework the GATE CALL SITE.** Replace the current block (the `if html:` … `_homepage_gambling_gate(domain, html)` … `if failed:` … early-return, roughly the lines from `_gate_buckets: dict = {}` through the gate's `return result`) with:

```python
    from services import recommendation_service as rec
    _gate_buckets: dict = {}
    _gate_verdicts: list[dict] = []
    _gate_offending: list[dict] = []
    if html:
        _gate_offending, _gate_buckets, _gate_verdicts = _homepage_gambling_gate(domain, html)
        result.homepage_scraped = True
        # Fast-path: homepage ALONE already links to >= threshold distinct sites → Skip now.
        _hp_domains = rec.confirmed_pg_domains(_gate_offending, [])
        if len(_hp_domains) >= settings.PORN_GAMBLE_SKIP_THRESHOLD:
            result.bad_links_found = _gate_offending
            result.early_failed = True
            result.early_fail_reason = f"Linking to {len(_hp_domains)} porn/gamble sites"
            result.recommendation = {
                "decision": "SKIP",
                "reason": result.early_fail_reason,
                "flags": [],
                "steps": {
                    "homepage_gate": {"status": "PASS", "detail": ""},
                    "porn_gamble_links": {"status": "FAIL", "count": len(_hp_domains), "examples": _hp_domains[:5]},
                },
            }
            result.risk_level = rec.derive_risk_level("SKIP")
            result.ai_analysis = {"summary": result.early_fail_reason, "risk_level": "HIGH"}
            logger.warning("[%s] SKIP — homepage links to %d porn/gamble site(s).", domain, len(_hp_domains))
            return result
```
(The `from services import recommendation_service as rec` may already be imported later in the function; ensure `rec` is available here. If a duplicate import warning is a concern, keep just this one near the top of `audit_domain` and remove later redundant `from services import recommendation_service as rec` lines.)

- [ ] **Step 3: Merge the gate's AI-gambling links into homepage processing.** In the homepage `if html:` processing block, right AFTER `result.bad_links_found = [ ... from link_result ... ]`, add a merge and REMOVE the `keyword_link_flags` line. Replace:

```python
        result.competitor_links_found = link_checker.check_competitor_links(html, full_url)
        # Secondary: keyword signals in external links only
        result.keyword_link_flags = link_checker.keyword_links_present(html, porn_kws[:30], source_domain=domain)
```
with:

```python
        # Merge the gate's AI-detected homepage gambling/adult links (not in the known-bad list).
        _existing = {b["found_href"] for b in result.bad_links_found}
        for _o in _gate_offending:
            if _o.get("found_href") and _o["found_href"] not in _existing:
                result.bad_links_found.append(_o)
                _existing.add(_o["found_href"])
        result.competitor_links_found = link_checker.check_competitor_links(html, full_url)
```

- [ ] **Step 4: Drop the keyword heuristic in the deep crawl.** In `_deep_check_page`, DELETE these two assignments:

```python
                # External-only keyword flags (skip internal links)
                check_entry["keyword_flags"] = link_checker.keyword_links_present(
                    page_html, porn_kws[:30], source_domain=domain
                )
                check_entry["gambling_keyword_links"] = link_checker.gambling_keyword_external_links(
                    page_html, porn_kws, domain, oc_legit
                )
```
(Leave the AI `classify_outbound_links` block that follows — it now judges domains.)

- [ ] **Step 5: Replace the porn/gamble decision block** (the `# ── Decision: porn/gamble outbound links → SKIP` block that builds `_bad_hrefs`/`_kw_hrefs`/`_pg_hits` and returns) with the count-based decision:

```python
    # ── Decision: porn/gamble outbound links (distinct confirmed destination sites) ─
    _pg_domains = rec.confirmed_pg_domains(result.bad_links_found, result.deep_page_checks)
    _pg_decision, _pg_reason, _pg_flag = rec.decide_porn_gamble(_pg_domains, settings.PORN_GAMBLE_SKIP_THRESHOLD)
    if _pg_decision:
        _pg_status = "FAIL" if _pg_decision == "SKIP" else "WARN"
        result.recommendation = {
            "decision": _pg_decision,
            "reason": _pg_reason,
            "flags": ([_pg_flag] if _pg_flag else []),
            "steps": {
                "homepage_gate": {"status": "PASS", "detail": ""},
                "porn_gamble_links": {"status": _pg_status, "count": len(_pg_domains), "examples": _pg_domains[:5]},
            },
        }
        result.risk_level = rec.derive_risk_level(_pg_decision)
        logger.info("[%s] Recommendation: %s — %s.", domain, _pg_decision, _pg_reason)
        return result
```

- [ ] **Step 6: Remove the now-unused `oc_legit`.** It was only used by the deleted `gambling_keyword_external_links` calls. Delete the two lines near the top of `audit_domain`:

```python
    from services import outbound_classifier as _oc
    oc_legit = _oc._load_legit_domains()
```
(If `_oc`/`oc_legit` is referenced anywhere else, leave them — grep `oc_legit` first; expect no remaining references after Steps 4–5.)

- [ ] **Step 7: Verify**

1. `.venv/Scripts/python.exe -c "import audit.audit_engine; print('ok')"` → `ok`
2. `grep -n "gambling_keyword_links\|gambling_keyword_external_links\|keyword_link_flags\|_kw_hrefs\|oc_legit" audit/audit_engine.py` → no output (all removed).
3. Full suite: `.venv/Scripts/python.exe -m pytest -q` → 29 passed.

- [ ] **Step 8: Commit**

```bash
git add audit/audit_engine.py
git commit -m "feat: count-based porn/gamble decision; gate no instant-skip; drop keyword heuristic"
```

## Escalation
If the gate/call-site/decision blocks differ from the description, STOP and report NEEDS_CONTEXT with the actual code.

---

## Task 5: UI — remove keyword displays, add WARN emoji

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Remove the keyword "external gambling/adult link(s)" displays.** In the Links & Page Check tab, find and delete the blocks that render `keyword_flags` / `keyword_link_flags` (the ones titled like "external gambling/adult link(s)" iterating over `all_kw_flags_hp` / `pg_kw` / `check.get("keyword_flags")`). Read the tab and remove every block that displays these keyword-flag lists, plus their aggregation lines (`all_kw_flags_hp`, `all_kw_flags_dp`, `keyword_flags` references). Leave the bad-link (known + AI) displays intact.

> **Note for implementer:** grep `keyword_flag` and `keyword_link_flags` in `app.py` and remove each render/aggregation usage. If removing an aggregation variable would break a later count (e.g. `total_pg_issues`), recompute that count without the keyword-flag terms.

- [ ] **Step 2: Add the WARN status emoji** to the phase-panel emoji map (the `_ph_emoji = {...}` dict) so the 1–2 manual case renders:

```python
                         "WARN": "🟠",
```
(add this key to the existing `_ph_emoji` dict).

- [ ] **Step 3: Verify**

1. `.venv/Scripts/python.exe -m py_compile app.py` → no error.
2. `.venv/Scripts/python.exe -c "import ast; ast.parse(open('app.py',encoding='utf-8').read()); print('parse ok')"` → `parse ok`.
3. `grep -n "keyword_flag\|external gambling/adult link" app.py` → no output.
4. Full suite green. Do NOT launch streamlit.

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "ui: remove keyword gambling/adult link displays; WARN phase emoji"
```

## Escalation
If `keyword_flags` removal threatens to break a counter or layout you can't cleanly resolve, STOP and report NEEDS_CONTEXT.

---

## Task 6: Docs + regression

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Full regression** — `.venv/Scripts/python.exe -m pytest -q`. Expect all green. If anything fails, STOP and report BLOCKED.

- [ ] **Step 2: Document** — read `README.md`, then update the porn/gambling section to state: links are judged by the **destination domain's nature** (a site that writes ABOUT porn/gambling is not flagged; only actual operators are); the naive keyword word-match was removed; the verdict is now **count-based** — `PORN_GAMBLE_SKIP_THRESHOLD`+ distinct sites → Skip, 1–2 → Check manually, 0 → continue. Add `PORN_GAMBLE_SKIP_THRESHOLD` to the env-var docs. Keep it concise.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: destination-based porn/gamble detection + count threshold"
```

---

## Self-Review Notes (for the implementer)

- **Task 4 is the crux.** Order matters: the gate runs first (fast-path only); homepage processing merges the gate's AI-gambling links into `bad_links_found`; the deep crawl runs; then the count-based decision over distinct domains.
- **`bad_links_found` is rebuilt** by homepage processing (known-bad), then the gate's AI-gambling entries are merged in (Step 3) so homepage AI gambling links aren't lost.
- **`porn_gamble_hits`** (old helper) is now unused but its test stays — leave it; don't delete.
- **Expected on fenced.ai:** the adult false positives (webroot/statista/etc.) disappear; the ~4 real gambling domains remain → 4 ≥ 3 → still SKIP (correct).

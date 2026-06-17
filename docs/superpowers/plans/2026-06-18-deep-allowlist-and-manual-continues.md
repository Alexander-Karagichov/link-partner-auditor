# Deep-Classifier Allowlist + CHECK_MANUALLY Continues — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the deep classifier flagging allowlisted/social domains (and counting the vague `other_harmful` category), and let a porn/gamble CHECK_MANUALLY run PBN + content-farm instead of short-circuiting.

**Architecture:** A one-line prompt change in `llm_service`; in `audit_domain` re-add the legit-allowlist load, filter the deep-page classifier's input + outputs, and change the porn/gamble step so only SKIP returns while CHECK_MANUALLY stashes its verdict and the headline builder folds it in.

**Tech Stack:** Python 3, pytest. LLM via `_backend.chat_json`.

**Spec:** `docs/superpowers/specs/2026-06-18-deep-allowlist-and-manual-continues-design.md`

**Conventions:** run tests with `.venv/Scripts/python.exe -m pytest ...`; use the **Bash** tool; do NOT pip install.

---

## Task 1: Drop `other_harmful` from the deep classifier prompt

**Files:**
- Modify: `services/llm_service.py`

- [ ] **Step 1: Edit `classify_outbound_links`'s prompt.** Find this exact line in its `prompt_parts` (the category enum):

```python
            "`domain`, `category` (gambling|social_casino|sports_betting|adult|escort|other_harmful), "
```
Replace with:
```python
            "`domain`, `category` (gambling|social_casino|sports_betting|adult|escort), "
```

- [ ] **Step 2: Verify**

Run: `.venv/Scripts/python.exe -c "from services import llm_service; print('ok')"` → `ok`
Run: `.venv/Scripts/python.exe -m pytest -q` → 31 passed.

- [ ] **Step 3: Commit**

```bash
git add services/llm_service.py
git commit -m "feat: drop other_harmful category from deep gambling classifier"
```

---

## Task 2: audit_domain — deep allowlist/category filter + CHECK_MANUALLY continues

**Files:**
- Modify: `audit/audit_engine.py`

Read `audit_domain`, the deep-crawl `_deep_check_page` (the `body_external_links = link_checker.extract_body_external_links(...)` + `classify_outbound_links` + merge block), the porn/gamble decision block (`_pg_domains = rec.confirmed_pg_domains(...)` … `if _pg_decision:` … `return result`), and the headline builder (`# ── Build the headline recommendation …` with `decide_after_scores`).

- [ ] **Step 1: Re-add the legit-allowlist load.** Near the top of `audit_domain`, right after `porn_kws = get_porn_gambling_keywords()` (or the line that defines `porn_kws`), add:

```python
    from services import outbound_classifier as oc
    oc_legit = oc._load_legit_domains()
```

- [ ] **Step 2: Filter the deep classifier's input + outputs.** In `_deep_check_page`, replace this block:

```python
                body_external_links = link_checker.extract_body_external_links(page_html, page_url)
                if body_external_links:
                    logger.info(
                        "[%s] AI-classifying %d body external link(s) on %s…",
                        domain, len(body_external_links), page_url,
                    )
                    ai_flagged = ai_service.classify_outbound_links(page_url, body_external_links)
                    check_entry["ai_flagged_links"] = ai_flagged
                    # Merge AI-flagged links into bad_links so they surface in the UI
                    existing_hrefs = {b["found_href"] for b in check_entry["bad_links"]}
                    for flagged in ai_flagged:
                        href = flagged.get("found_href", "")
                        if href and href not in existing_hrefs:
                            check_entry["bad_links"].append({
                                "found_href": href,
                                "matched_bad_domain": f"[AI: {flagged.get('category', 'harmful')}]",
                                "link_text": flagged.get("reason", ""),
                            })
                            existing_hrefs.add(href)
```
with:

```python
                body_external_links = link_checker.extract_body_external_links(page_html, page_url)
                # Never classify allowlisted (legit) destinations — social share buttons,
                # facebook/twitter, etc. must never be flagged as gambling/adult.
                body_external_links = [
                    u for u in body_external_links
                    if not oc.is_legit_domain(link_checker._extract_domain(u), oc_legit)
                ]
                if body_external_links:
                    logger.info(
                        "[%s] AI-classifying %d body external link(s) on %s…",
                        domain, len(body_external_links), page_url,
                    )
                    ai_flagged = ai_service.classify_outbound_links(page_url, body_external_links)
                    check_entry["ai_flagged_links"] = ai_flagged
                    # Merge only true gambling/adult flags into bad_links (ignore vague catch-alls).
                    _PG_CATS = {"gambling", "social_casino", "sports_betting", "adult", "escort"}
                    existing_hrefs = {b["found_href"] for b in check_entry["bad_links"]}
                    for flagged in ai_flagged:
                        if flagged.get("category") not in _PG_CATS:
                            continue
                        href = flagged.get("found_href", "")
                        if href and href not in existing_hrefs:
                            check_entry["bad_links"].append({
                                "found_href": href,
                                "matched_bad_domain": f"[AI: {flagged.get('category', 'gambling')}]",
                                "link_text": flagged.get("reason", ""),
                            })
                            existing_hrefs.add(href)
```

- [ ] **Step 3: Make CHECK_MANUALLY continue (decision block).** Replace the porn/gamble decision's build-and-return block:

```python
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
with:

```python
    _pg_manual = None
    if _pg_decision == "SKIP":
        result.recommendation = {
            "decision": "SKIP",
            "reason": _pg_reason,
            "flags": ([_pg_flag] if _pg_flag else []),
            "steps": {
                "homepage_gate": {"status": "PASS", "detail": ""},
                "porn_gamble_links": {"status": "FAIL", "count": len(_pg_domains), "examples": _pg_domains[:5]},
            },
        }
        result.risk_level = rec.derive_risk_level("SKIP")
        logger.info("[%s] Recommendation: SKIP — %s.", domain, _pg_reason)
        return result
    elif _pg_decision == "CHECK_MANUALLY":
        _pg_manual = {"reason": _pg_reason, "flag": _pg_flag,
                      "count": len(_pg_domains), "examples": _pg_domains[:5]}
        logger.info("[%s] porn/gamble → manual; continuing to PBN/spam.", domain)
```

- [ ] **Step 4: Fold `_pg_manual` into the headline builder.** In the headline builder, change the block from `_decision, _reason = rec.decide_after_scores(...)` through `result.recommendation = {...}`. After the `decide_after_scores(...)` call, insert the override, and change the `porn_gamble_links` step. Replace:

```python
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
```
with:

```python
    _decision, _reason = rec.decide_after_scores(
        pbn_band=_pbn_band, pbn_score=_pbn_score,
        content_farm_band=_cf_band, content_farm_score=_cf_score,
    )
    if _pg_manual:
        _decision = "CHECK_MANUALLY"
        _reason = _pg_manual["reason"]
        _extra = []
        if _pbn_band == "HIGH":
            _extra.append(f"High PBN risk (score {_pbn_score})")
        if _cf_band == "HIGH":
            _extra.append(f"High content-farm risk (score {_cf_score})")
        if _extra:
            _reason = _reason + "; " + "; ".join(_extra)
        if _pg_manual["flag"]:
            _flags = [_pg_manual["flag"]] + _flags
    result.recommendation = {
        "decision": _decision,
        "reason": _reason,
        "flags": _flags,
        "steps": {
            "homepage_gate": {"status": "PASS", "detail": ""},
            "porn_gamble_links": {
                "status": "WARN" if _pg_manual else "PASS",
                "count": _pg_manual["count"] if _pg_manual else 0,
                "examples": _pg_manual["examples"] if _pg_manual else [],
            },
            "pbn": {"status": "FAIL" if _pbn_band == "HIGH" else "PASS", "band": _pbn_band, "score": _pbn_score},
            "content_farm": {"status": "FAIL" if _cf_band == "HIGH" else "PASS",
                             "band": _cf_band, "score": _cf_score,
                             "semrush_checked": (result.content_farm or {}).get("semrush_checked", False)},
        },
    }
```

- [ ] **Step 5: Verify**

1. `.venv/Scripts/python.exe -c "import audit.audit_engine; print('ok')"` → `ok`
2. `grep -n "_pg_manual\|oc.is_legit_domain\|_PG_CATS" audit/audit_engine.py` → shows the new code.
3. `grep -n "other_harmful" audit/audit_engine.py` → no output (the merge no longer special-cases it; the fallback string is now `'gambling'`).
4. `.venv/Scripts/python.exe -m pytest -q` → 31 passed.

- [ ] **Step 6: Commit**

```bash
git add audit/audit_engine.py
git commit -m "feat: deep allowlist + category filter; CHECK_MANUALLY runs PBN/spam"
```

## Escalation
If the deep-crawl block, decision block, or headline builder differs from what's shown, STOP and report NEEDS_CONTEXT with the actual code.

---

## Task 3: Docs + regression

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Full regression** — `.venv/Scripts/python.exe -m pytest -q`. Expect 31 passed. If anything fails, STOP and report BLOCKED.

- [ ] **Step 2: Document** — read `README.md`, then add a concise note: the deep link classifier ignores allowlisted/social domains (share buttons are never flagged) and only counts true gambling/adult categories; and a **Check-manually** verdict still runs the PBN and content-farm checks so reviewers see full scores (only **Skip** short-circuits). Keep it brief.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: deep allowlist + Check-manually runs PBN/spam"
```

---

## Self-Review Notes (for the implementer)

- **`_pg_manual` scope:** initialized in the decision block (Step 3) to `None`; read in the headline builder (Step 4). Both are in `audit_domain`'s body — no function boundary between them.
- **SKIP still returns** (Step 3); only CHECK_MANUALLY continues. The "couldn't-fetch" CHECK_MANUALLY paths return earlier and are untouched.
- **`oc` / `oc_legit`** are defined at the top of `audit_domain` (Step 1) and closed over by the nested `_deep_check_page` (Step 2).
- **Expected:** fenced.ai → facebook/twitter gone (3 gambling domains, handled by the existing 3+/promoter logic); a 1–2-link domain → CHECK_MANUALLY with PBN + content-farm scores populated in the panel and HIGH findings appended to the reason.

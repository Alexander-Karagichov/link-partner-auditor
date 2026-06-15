# Spec: Phase Panel, Early Niche, and Spam/PBN Rubric

**Date:** 2026-06-15
**Status:** Draft for review

## Background

The audit now ends on a Skip / Check manually / Approved verdict, but the detailed
results show only a terse one-line scorecard, and when the tree short-circuits
(e.g. SKIP at porn/gamble) the later phases simply vanish — the user can't tell
they were skipped or why. The content-farm ("spam") score is computed but there's
no visible breakdown of how it was reached. And the website niche, which IS
produced by the end-stage AI analysis, is missing on SKIP results because that
analysis never runs.

## Goals

1. **Early niche** — determine the site's niche right after the homepage gate
   passes, so it shows even when the audit later SKIPs. Display-only.
2. **Phase panel** — a clear vertical breakdown of every phase in the detailed
   results, with skipped phases labelled with the reason.
3. **Spam/PBN rubric** — an expandable "why" under the PBN and Spam phases showing
   the reasons, key signals, and band thresholds.
4. **Declutter** — remove the now-redundant standalone PBN-risk banner,
   content-farm-risk banner, and derived "Risk: …" line (the verdict + phase panel
   replace them).

## Non-goals (YAGNI)

- Niche never gates or changes the recommendation; no client-niche comparison.
- No change to how PBN or content-farm scores are computed — only how they're shown.

---

## 1. Early niche detection

**New LLM function** in `services/llm_service.py`:

```python
def determine_niche(homepage_text: str, about_text: str = "") -> str:
    """Return a 3-6 word niche/topic description of the site, or "" on empty/error."""
```
Mirrors the existing `_backend.chat_json` + `_parse_json` pattern; returns
`parsed.get("niche", "")` (short string). Returns "" if `homepage_text` is empty.

**New field** on `AuditResult`: `niche: str = ""` (+ `to_dict` entry `"niche": self.niche`).

**Placement in `audit_domain`:** right AFTER the homepage-processing `if html:`
block (so `result.homepage_text` / `result.about_page_text` are set) and BEFORE the
SERP-results/deep-crawl/Step-3 block, so it's set on every homepage-passing path
including a later porn/gamble SKIP:

```python
    if result.homepage_text:
        result.niche = ai_service.determine_niche(result.homepage_text, result.about_page_text or "")
```

The end-stage `analyze_audit` still runs for non-skipped results and still yields
`ai_analysis["website_niche"]`; the UI prefers `result.niche` and falls back to it.

## 2. Phase panel (pure helper + UI)

**New pure helper** in `services/recommendation_service.py` (unit-tested):

```python
def build_phase_rows(steps: dict, niche: str) -> list[dict]:
    """
    Ordered display rows for the phase panel:
      [{"name", "status", "detail"}], status ∈
      PASS | FAIL | SKIPPED | INFO | LOW | MEDIUM | HIGH.
    Skipped phases derive their reason from where the tree stopped.
    """
```

Logic:
- **Homepage gate** — from `steps["homepage_gate"]` (PASS/FAIL + detail).
- **Niche** — INFO row, detail = `niche or "—"`.
- Compute the skip reason once: homepage FAIL → "didn't pass homepage check";
  else if `porn_gamble_links` present and FAIL → "failed P/G links check";
  else → "couldn't fetch data".
- **P/G links** — from `steps["porn_gamble_links"]` (PASS, or FAIL with
  `{count} found`); if absent → SKIPPED + reason.
- **PBN** — from `steps["pbn"]` (status = band, detail = `score {n}`); if absent →
  SKIPPED + reason.
- **Spam / content farm** — from `steps["content_farm"]` likewise; if absent →
  SKIPPED + reason.

**UI** (`app.py`, in the per-result detail render): replace the current `•`-joined
scorecard caption with the vertical panel. An emoji map covers every status:
`PASS→🟢, FAIL→🔴, SKIPPED→➖, INFO→🏷️, LOW→🟢, MEDIUM→🟠, HIGH→🔴`. Each row renders
`{emoji} {name} — {status} {detail}`. The verdict banner and flags (already present)
stay above it.

## 3. Expandable "why" under PBN & Spam

After the PBN row, if `result.pbn` is populated, render an `st.expander("Why — PBN
score")` containing:
- band + score,
- the reasons (`result.pbn["reasons"]`) as bullets,
- the key signals (`result.pbn["signals"]`) as a compact line,
- the band thresholds: **PBN — LOW <20 · MEDIUM 20–44 · HIGH 45+**.

After the Spam row, if `result.content_farm` is populated, render an
`st.expander("Why — Spam/content-farm score")` containing:
- band + score,
- the reasons (`result.content_farm["reasons"]`),
- the key signals (`result.content_farm["signals"]`) + whether SEMrush was checked
  (`semrush_checked`),
- the band thresholds: **Content-farm — LOW <25 · MEDIUM 25–54 · HIGH 55+**.

Both are pure display of fields that already exist; nothing is recomputed. The
thresholds are written to match the current scoring code
(`pbn_service.compute_signals`: ≥45 HIGH, ≥20 MEDIUM; `content_farm_service.compute_signals`:
≥55 HIGH, ≥25 MEDIUM).

## 4. Remove redundant elements (`app.py`)

Delete from the per-result detail render:
- the standalone **PBN-risk** banner (`if result.pbn and result.pbn.get("pbn_risk")` block),
- the standalone **Content-Farm Risk** banner (`if cfarm.get("content_farm_risk")` block),
- the derived **"Risk: …"** markdown line.

The recommendation banner + phase panel are now the single verdict view. Keep the
AI summary / niche / competitor block in the SEO Metrics tab and the summary table
(its `Niche` column should prefer `result.niche`, falling back to
`ai_analysis["website_niche"]`).

## New surface area

- `services/llm_service.py`: `determine_niche`.
- `services/recommendation_service.py`: `build_phase_rows`.
- `AuditResult.niche` (+ to_dict).
- `audit_engine.audit_domain`: one niche call after homepage processing.
- `app.py`: phase panel + PBN/Spam expanders; remove three banners; summary `Niche`
  prefers `result.niche`.

## Testing

Unit tests for `build_phase_rows`:
- all-phases-ran (APPROVED) → 5 rows, PBN/Spam show bands.
- SKIP at P/G → PBN & Spam rows are SKIPPED with "failed P/G links check".
- SKIP at homepage → P/G/PBN/Spam SKIPPED with "didn't pass homepage check".
- niche INFO row shows the niche, or "—" when empty.

`determine_niche` and the UI are not unit-tested (LLM/Streamlit), per the codebase
convention; verify via import + `py_compile` + a manual smoke run.

## Open implementation questions (decide during plan)

- Exact compact formatting of the signals line in the expanders.
- Whether `determine_niche` should also feed `analyze_audit` to avoid its internal
  niche derivation (minor; left as-is unless trivial).

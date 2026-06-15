# Spec: Partner Recommendation Engine (Skip / Check Manually / Approved)

**Date:** 2026-06-15
**Status:** Draft for review

## Background

Today the audit produces a `risk_level` label (NO_RISK / CLEAN / LOW / MEDIUM /
HIGH / CRITICAL) via a rule-based override block in `audit_domain`, plus separate
PBN and content-farm scores. There's no single, action-oriented verdict telling
the user what to *do* with a link partner, and the pipeline runs every expensive
check even on domains that are already disqualified.

## Goal

Replace `risk_level` with a single headline **recommendation** — **Skip**,
**Check manually**, or **Approved** — produced by a **sequential decision tree**
that short-circuits at the first disqualifying check (saving SEMrush/LLM/scrape
cost). Every step still exposes its own score, and noteworthy-but-non-blocking
findings surface as **flags** even on an Approved result.

## The recommendation object

New field on `AuditResult`:

```python
recommendation: dict = field(default_factory=dict)
# {
#   "decision": "SKIP" | "CHECK_MANUALLY" | "APPROVED",
#   "reason": str,                 # short human reason, e.g. "Linking to porn/gamble websites"
#   "flags": list[str],            # non-blocking notes shown on APPROVED/CHECK_MANUALLY
#   "steps": {                     # per-step scorecard, always populated as far as the run got
#       "homepage_gate": {"status": "PASS"|"FAIL"|"SKIPPED", "detail": str},
#       "porn_gamble_links": {"status": ..., "count": int, "examples": [..]},
#       "pbn": {"status": "PASS"|"FAIL"|"SKIPPED", "band": str, "score": int},
#       "content_farm": {"status": ..., "band": str, "score": int, "semrush_checked": bool},
#   },
# }
```

## Decision tree (runs inside `audit_domain`, short-circuits)

The tree replaces the rule-based `risk_level` override block (section 7b). Each
arrow that ends a branch means: stop, set `recommendation`, skip all remaining
work (including the anchor/link-building recommendation), and return.

**Step 1 — Homepage gate** (already exists as the gambling/porn hard-fail).
The gate fires if the homepage links to gambling/porn via **any** of: a
`known_bad_sites.txt` match, an AI domain classification, **or** an anchor-text
porn/gamble keyword pointing to a non-allowlisted external domain (NEW — add the
`keyword_links_present` signal, allowlist-filtered, into the gate).
→ FAIL → **SKIP**, reason `"Failed homepage check"`. `steps.homepage_gate = FAIL`.

**Step 2 — Data sufficiency.** If the homepage could not be scraped (no `html`),
the gate couldn't run meaningfully.
→ **CHECK_MANUALLY**, reason `"Couldn't fetch homepage"`.
Later, if SEMrush/overview returned an error AND no usable data, same treatment:
**CHECK_MANUALLY**, reason `"Couldn't fetch SEO data"`.

**Step 3 — Porn/gamble outbound links (deep pages).** Run the SEMrush rankings +
deep-page crawl (needed to discover and check pages). If **any** outbound link to
gambling/porn is found on a deep page — same three detectors as Step 1
(known-bad OR AI-domain OR anchor-keyword→non-allowlisted external), even one —
→ **SKIP**, reason `"Linking to porn/gamble websites"`.
`steps.porn_gamble_links = FAIL (count=N)`. Skip PBN, content-farm, anchor.

**Step 4 — PBN + spam (content-farm).** Run both. A HIGH band on either is a fail.
→ **CHECK_MANUALLY**, reason `"High PBN risk (score N)"` and/or
`"High content-farm risk (score N)"` (combine if both). Still attach flags.

**Step 5 — Approved.** Nothing disqualifying.
→ **APPROVED**. Generate the anchor/link-building recommendation (Approved only).

In all reached steps, populate `steps.*` so the scorecard is always visible.

## Flags (non-blocking; collected throughout; shown on APPROVED and CHECK_MANUALLY)

Flags never change the decision on their own — they annotate it:

- **Competitor link** — `competitor_links_found` is non-empty → `"Links to a competitor"`.
- **Young + thin** — domain age < 6 months AND organic traffic < 1,000 →
  `"New domain (<6mo) with low traffic (<1k/mo)"`.
- **PBN MEDIUM** — `pbn.pbn_risk == "MEDIUM"` → `"Some PBN signals (score N)"`.
- **Content-farm MEDIUM** — `content_farm.band == "MEDIUM"` → `"Some content-farm signals (score N)"`.

(Thresholds — 6 months, 1k traffic — are config constants, tunable.)

## Per-step scores (always displayed)

Status + each step's natural metric, no artificial 0–100 on the binary gates:

| Step | Score shown |
|------|-------------|
| Homepage gate | PASS / FAIL (+ offending links on FAIL) |
| Porn/gamble links | PASS / FAIL + count of bad links found |
| PBN | 0–100 score + LOW/MEDIUM/HIGH band |
| Content-farm | 0–100 score + LOW/MEDIUM/HIGH band (+ "SEMrush skipped" note) |

A step not reached (because an earlier step short-circuited) shows `SKIPPED`.

## Pipeline reorder (implementation consequence)

To short-circuit before the expensive checks, the **deep-page crawl must run
before PBN and content-farm** (today content-farm runs before the deep crawl).
New order inside `audit_domain`:

1. Homepage gate (Step 1) — unchanged, returns early on fail.
2. Wave 1 data collection (overview, backlinks, SERP, DNS) + homepage processing
   (link checks → `bad_links_found`, `keyword_link_flags`, competitor links).
3. SEMrush keyword checks + **deep-page crawl** → check Step 3 porn/gamble links →
   short-circuit SKIP if found.
4. Reciprocity/legitimacy + PBN verdict + content-farm (Step 4) → CHECK_MANUALLY on HIGH.
5. Build `recommendation` (decision + reason + flags + steps).
6. Anchor recommendation — **only if APPROVED**.

## Retiring `risk_level`

The rule-based override block (Rules 1–3: CRITICAL/NO_RISK/LOW) is **removed**;
`recommendation` is the headline verdict. For backward compatibility and minimal
UI churn, keep the `risk_level` field but **derive** it from the decision
(`SKIP → "HIGH"`, `CHECK_MANUALLY → "MEDIUM"`, `APPROVED → "LOW"`) so existing
serialization/summary code keeps working; the UI's primary verdict becomes the
recommendation. The AI `analyze_audit` summary text is retained (informational).

## New module

`services/recommendation_service.py` — **pure** decision logic, unit-tested:

- `collect_flags(*, competitor_links: list, age_days: Optional[int],
  organic_traffic: Optional[int], pbn_band: Optional[str],
  content_farm_band: Optional[str], young_days: int, low_traffic: int) -> list[str]`
- `decide_after_scores(*, pbn_band: Optional[str], content_farm_band: Optional[str],
  flags: list[str]) -> tuple[str, str]` — returns `(decision, reason)`:
  HIGH on either → `("CHECK_MANUALLY", reason)`, else `("APPROVED", "")`.
- `derive_risk_level(decision: str) -> str` — maps decision → legacy band.

The SKIP/short-circuit decisions (Steps 1–3) are set inline in `audit_domain`
(they're tied to fetching), but the Step-4/5 outcome and flags come from this
pure module so they're testable without network.

## UI

A prominent recommendation banner at the top of each result, above the tabs:
- 🔴 **SKIP** / 🟠 **CHECK MANUALLY** / 🟢 **APPROVED** + the reason.
- Flags listed beneath as bullet captions.
- The per-step scorecard (gate, porn/gamble, PBN, content-farm) rendered as a
  compact status row; existing tabs/banners kept below.
- Summary/results table: replace the `risk_level` column with a `Recommendation`
  column (SKIP/MANUAL/APPROVED).

## Config (new)

```python
RECO_YOUNG_DOMAIN_DAYS: int = int(os.getenv("RECO_YOUNG_DOMAIN_DAYS", "180"))   # <6 months
RECO_LOW_TRAFFIC: int = int(os.getenv("RECO_LOW_TRAFFIC", "1000"))
```
(PBN/content-farm HIGH=fail, MEDIUM=flag are fixed by the band semantics, not config.)

## Non-goals (YAGNI)

- No fetching of unknown outbound link destinations (decided: known list + AI
  domain-judge + anchor-keyword is enough).
- No change to how PBN or content-farm compute their scores — only their bands
  are consumed here.
- No numeric 0–100 forced onto the binary gate steps.

## Testing

Unit tests for `recommendation_service` (`collect_flags`, `decide_after_scores`,
`derive_risk_level`) covering: HIGH→manual, MEDIUM→flag+approved, both-clean→
approved, each flag condition, young+thin requiring BOTH conditions. The
short-circuit ordering in `audit_domain` is verified by reading + a manual smoke
run (the network path isn't unit-tested, consistent with the codebase).

## Open implementation questions (decide during plan)

- Exact reason-string format when multiple Step-4 fails combine.
- Whether `keyword_links_present` needs an allowlist parameter added, or the
  allowlist filter is applied by the caller in `audit_domain`.

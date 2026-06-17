# Spec: Context Refinements (directory, subdomain, skip-reason, service-pages)

**Date:** 2026-06-17
**Status:** Draft for review

## Background

After the destination-domain detection fix, a new batch surfaced four false-positive
/ wrong-label cases:

1. **clodura.ai → SKIP (13 sites).** Every flagged link is on a
   `/directory/company/<name>` page (casino-admiral, georgia-lottery, oregon-lottery,
   winstar-casino…). clodura is a B2B sales-intelligence **directory**; its company
   profiles link to the profiled companies, some of which are casinos/lotteries
   (several are government lotteries). The links are real but **incidental to a
   directory**, not affiliate promotion.
2. **xavor.com → SKIP (10 sites).** All 10 are on `china.xavor.com` — a spammy
   **subdomain**. The main site is a legit engineering firm; Google treats subdomains
   as separate sites.
3. **zazz.io → CHECK_MANUALLY (1 gambling link).** Correct verdict, but the phase
   panel mislabels the skipped PBN/Spam rows as *"couldn't fetch data"* — a
   `build_phase_rows` bug (it doesn't handle the new `WARN` status).
4. **vrinsofts.com → content-farm MEDIUM (60).** The article-quality AI flagged its
   **service pages** (`ai-ml-development-company.html`, `software-development.html`,
   `digital-transformation-services.html`) as 75 % "trivia/low-value filler." They're
   legit B2B service pages, not content-farm articles.

## Goals

Stop false-skipping/false-flagging legit businesses while still catching real gambling
affiliates and content farms.

## Non-goals (YAGNI)

- No change to how the destination domain itself is classified (that fix stands).
- No fetching beyond what already happens.

---

## 1. clodura — AI "promoter vs. incidental" check before Skip

The count-based decision stands, but the **SKIP** outcome (≥ `PORN_GAMBLE_SKIP_THRESHOLD`
distinct gambling domains) gets one verification step.

**New LLM function** `services/llm_service.py`:

```python
def classify_gambling_link_context(audited_domain: str, niche: str,
                                   source_pages: list[str], gambling_domains: list[str]) -> str:
    """Return "promoter" or "incidental"."""
```
Prompt: given the audited site's domain + niche, the source-page URLs where the gambling
links were found, and the gambling destination domains, decide whether the site is a
**gambling promoter / affiliate / casino-review site** (it exists to push gambling) vs a
**neutral directory / company database / news / B2B / safety site** that links to gambling
companies **incidentally** (profiling, reporting, listing). Return `{"context": "promoter"|"incidental"}`.
On error/empty → default `"incidental"` (so AI failures route to human review, never a false auto-skip).

**Decision flow** (in `audit_domain`, at the porn/gamble step):
- `N = len(confirmed_pg_domains)` (already excludes subdomain pages — see §2).
- `N >= threshold`:
  - run `classify_gambling_link_context(domain, result.niche, source_pages, pg_domains)`.
  - `"promoter"` → **SKIP**, reason `"Linking to {N} gambling sites"`.
  - `"incidental"` → **CHECK_MANUALLY**, reason `"Links to {N} gambling sites (likely incidental — review)"`, flag listing the domains.
- `1 <= N < threshold` → **CHECK_MANUALLY** (no AI call), reason `"Links to {N} porn/gamble site(s)"`.
- `N == 0` → continue.

`source_pages` = the homepage URL (if it held gambling links) + each
`deep_page_checks[*].page_url` whose `bad_links` is non-empty (e.g. clodura's
`/directory/company/...` URLs). Cap at ~15 for the prompt.

This only adds an LLM call when a domain would otherwise be SKIPped on gambling links —
rare. The pure decision split stays in `recommendation_service`; the context call is wired
in `audit_domain`.

## 2. xavor — drop other-subdomain pages from the deep crawl

Google treats subdomains as separate sites, so gambling content on `china.xavor.com`
must not fail `xavor.com`.

In `audit_domain`, where the deep-crawl page list (`pg_ranking_urls`) is built (from
SEMrush porn/gambling ranking-page URLs + SERP results), **filter out any URL whose host
is not the exact audited domain or its `www.` form**. A small pure helper (testable):

```python
def same_site(url: str, audited_domain: str) -> bool:
    """True iff url's host == audited_domain or www.audited_domain (NOT other subdomains)."""
```
placed in `services/recommendation_service.py` (or link_checker). Apply it to filter
`pg_ranking_urls` before scraping, so subdomain pages are never crawled and their links
never counted.

(Homepage links are always the exact domain, so they're unaffected.)

## 3. zazz — fix the phase-panel skip reason for WARN

`recommendation_service.build_phase_rows` derives a skip reason for phases that didn't run.
It currently only handles homepage-FAIL and porn/gamble-FAIL; the new `WARN` status
(1–2 / incidental → manual) falls through to "couldn't fetch data". Fix the ladder:

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
(The genuine data-failure case has no `porn_gamble_links` step, so it still reaches the
final `else` correctly.)

## 4. vrinsofts — sharpen the content-farm article-quality prompt

`services/llm_service.py:classify_article_quality` over-flags a legit company's service
pages as trivia. Add explicit guidance to its prompt:

> A legitimate business's **service / product / solution / landing / pricing / about /
> contact** pages are NOT content-farm filler — do NOT mark them `is_trivia`. Only flag
> genuine low-value **informational** trivia / SEO-bait articles (how-to listicles,
> "what is X" definitions, unit conversions, generic filler with no real expertise or
> commercial purpose).

No structural change — the article sampling stays; the judgment is sharpened (same
approach that fixed the gambling classifier).

## New surface area

- `services/llm_service.py`: `classify_gambling_link_context` (new); `classify_article_quality` prompt sharpened.
- `services/recommendation_service.py`: `same_site` helper; `build_phase_rows` WARN reason.
- `audit/audit_engine.py`: filter `pg_ranking_urls` to same-site (§2); run the promoter
  check at the SKIP threshold and downgrade incidental → CHECK_MANUALLY (§1); pass
  `source_pages`.
- No config changes (threshold unchanged).

## Testing

Unit tests (pure):
- `same_site`: `china.xavor.com/x` vs `xavor.com` → False; `xavor.com/x` and
  `www.xavor.com/x` → True; `notxavor.com` → False.
- `build_phase_rows`: a `porn_gamble_links` step with `status="WARN"` → PBN/Spam rows say
  "porn/gamble check sent to manual review" (not "couldn't fetch data").

The two LLM prompt changes (`classify_gambling_link_context`, sharpened
`classify_article_quality`) are verified by import + a manual re-run against the
clodura / vrinsofts cases (expect: clodura → CHECK_MANUALLY, xavor → continue/Approved,
vrinsofts service pages no longer trash).

## Open implementation questions (decide during plan)

- Exact cap on `source_pages` passed to the context prompt (≈15).
- Whether `same_site` lives in recommendation_service or link_checker_service (pure either way).

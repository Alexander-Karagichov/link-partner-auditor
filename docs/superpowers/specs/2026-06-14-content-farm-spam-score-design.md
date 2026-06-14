# Spec: Content-Farm Spam Score

**Date:** 2026-06-14
**Status:** Draft for review

## Background

The auditor already flags bad link partners (known-bad links, PBN/link-scheme,
gambling/porn). It does NOT detect **content farms** — sites that mass-produce
low-value trivia / SEO-bait articles ("how many seconds in a day", unit
conversions) to rank for cheap informational queries. A link from a content farm
is low quality even when the site isn't a PBN or gambling affiliate.

Key insight from brainstorming: **word count is the wrong discriminator.** A
trivia article can be 800 words and still be trash. The real signal is the
low-value/trivia *nature* of the content (judged by an LLM), with thin word count
as only a secondary trigger.

## Goal

Add a standalone **content-farm score** (0–100, LOW/MEDIUM/HIGH band + short LLM
rationale) shown in its own UI section like the PBN score, built from two checks
plus a cheap footprint signal — and structured so the expensive SEMrush call is
only spent on domains that already look suspicious.

## Non-goals (YAGNI)

- No full-site crawl. Article sampling is bounded (≤ 8 homepage-linked articles).
- No sitemap/publishing-cadence analysis in this version (was considered; deferred).
- No new third-party dependency.

## Cost model (the central constraint)

SEMrush `domain_organic` bills ~10 API units per row (matches existing code's
assumptions). So the design makes the SEMrush pull **conditional and bounded**:

- A typical legit domain spends **0 extra SEMrush units** (Check 1 skipped).
- A suspected farm spends ~**100 units** (top 10 rows, single market).

---

## Architecture (mirrors the PBN feature)

- New module `services/content_farm_service.py` — heuristic signal scoring + band.
- New LLM functions in `services/llm_service.py` — article/phrase quality judgments
  + final verdict.
- New helper in `services/link_checker_service.py` — extract homepage article links.
- New SEMrush function in `services/semrush_service.py` — top pages by traffic.
- `audit/audit_engine.py` orchestrates the cheap→escalate flow inside `audit_domain`
  (only when the gambling/porn gate passed; skipped entirely on `early_failed`).
- `AuditResult.content_farm` dict + `to_dict`; new UI section in `app.py`; config knobs.

## Flow inside `audit_domain` (after the homepage block, only if not `early_failed`)

```
if ENABLE_CONTENT_FARM and not result.early_failed and html:
    # ── Step 1: cheap evidence (no SEMrush units) ─────────────────────────
    article_links = link_checker.extract_internal_article_links(html, full_url)   # homepage, body only
    article_link_count = len(article_links)        # FREE structural signal (farms have many)
    sampled = article_links[: CONTENT_FARM_SAMPLE_ARTICLES]                        # ≤ 8
    articles = parallel scrape sampled (bounded pool, like reciprocity)
    # each article judged trash if LLM says trivia/low-value OR body words < CONTENT_FARM_THIN_WORDS
    check2 = classify_article_quality(articles)        # -> {trash_share, trash_examples, judged}

    keyword_footprint = overview.organic_keywords or 0  # already pulled; cheap

    # ── Step 2: escalate to SEMrush ONLY if suspicious ────────────────────
    escalate = (check2.trash_share >= CONTENT_FARM_ESCALATE_TRASH_SHARE) \
               or (article_link_count >= CONTENT_FARM_ARTICLE_LINK_COUNT) \
               or (keyword_footprint >= CONTENT_FARM_KEYWORD_FOOTPRINT)
    check1 = None
    if escalate:
        top_market = (overview.top_databases or ["us"])[0]                 # single #1 market only
        pages = semrush.get_top_traffic_pages(domain, top_market, CONTENT_FARM_TOP_PAGES)  # ~100 units
        check1 = classify_trivia_phrases([p.phrase for p in pages])        # -> {trivia_share, examples}

    # ── Step 3: score + verdict ───────────────────────────────────────────
    signals = content_farm_service.compute_signals(check1, check2, keyword_footprint, escalate)
    verdict = ai_service.assess_content_farm(signals)        # LOW/MED/HIGH + rationale
    result.content_farm = {score, band, rationale, ...}
```

## Check 1 — Top-traffic pages are trivia junk (conditional, ~100 SEMrush units)

- `semrush.get_top_traffic_pages(domain, database, limit)` — `type=domain_organic`,
  `display_sort=tr_desc` (traffic descending), `export_columns=Ph,Po,Nq,Ur,Tr`,
  `display_limit=limit`, one database (the domain's #1 traffic market). Returns a
  list of `OrganicKeyword` (reuse existing model; `Tr` is informational).
- `ai_service.classify_trivia_phrases(phrases) -> {"trivia_share": float, "examples": [..]}`
  — LLM rates what fraction are low-value trivia/informational-farm queries vs real
  topical/business queries.
- Only runs when Step 2 escalation fires.

## Check 2 — Homepage links to trash articles (cheap-first, ≤ 8 scrapes)

- `link_checker.extract_internal_article_links(html, base_url) -> list[str]` — internal
  links from the homepage **body** (reuse the nav/footer exclusion logic from
  `extract_body_external_links`, but keep INTERNAL links), filtered to article-like
  URLs: a path with depth ≥ 1 and a slug (e.g. `/how-many-seconds-in-a-day/`),
  excluding obvious non-articles (`/`, `/category/`, `/tag/`, `/contact`, `/about`,
  `/product`, pure query strings, file assets). Deduplicated by URL.
- Sample up to `CONTENT_FARM_SAMPLE_ARTICLES` (8), scrape each via `bdata.scrape_page`
  in a bounded `ThreadPoolExecutor` (pattern copied from the reciprocity pool, with a
  per-article try/except so one bad page never aborts the audit).
- For each scraped article, gather `title` + a content snippet (via
  `extract_page_text`) and the body word count.
- `ai_service.classify_article_quality(articles) -> {"trash_share": float, "judged": int,
  "trash_examples": [..]}` — an article is **trash if EITHER** the LLM judges it
  low-value trivia/SEO-bait **OR** its body word count < `CONTENT_FARM_THIN_WORDS`.

## Footprint signals (free, already available)

Two cheap structural signals, used both as escalation triggers and minor scoring nudges:

- **Homepage article-link count** — `len(article_links)` from the Check-2 extraction
  (computed before sampling, so it's free). A homepage densely packed with internal
  article links (≥ `CONTENT_FARM_ARTICLE_LINK_COUNT`, default 30) is a strong content-farm
  structural tell (e.g. technology.org's homepage lists dozens of trivia articles). A
  normal business homepage links to only a handful of internal pages.
- **Keyword footprint** — `overview.organic_keywords`; a very large footprint
  (≥ `CONTENT_FARM_KEYWORD_FOOTPRINT`) is consistent with mass-produced content. Weak on
  its own — the LLM judgments dominate.

## Scoring — `content_farm_service.compute_signals(...)`

Returns `{"score": int (0-100), "band": "LOW|MEDIUM|HIGH", "signals": {...}, "reasons": [..]}`.
Heuristic contributions (exact weights tunable during implementation; ordering fixed):

- `check2.trash_share` is the primary driver (homepage publishes trivia/trash).
- `check1.trivia_share` (when Check 1 ran) strongly confirms — biggest weight, since
  it proves the site earns *traffic* for junk.
- Homepage article-link count and large `organic_keywords` footprint — minor nudges.
- Bands: HIGH when both checks agree the site is farm-like; MEDIUM on partial/single
  evidence; LOW otherwise. Reconcile score↔band with a band floor (as PBN does) so the
  number and band never contradict.

`ai_service.assess_content_farm(signals) -> {"content_farm_risk": "LOW|MEDIUM|HIGH",
"reasoning": str}` — LLM reasons over the signals + examples for the final verdict, which
takes precedence; the heuristic provides a floor.

The content-farm band also nudges the overall audit risk (a HIGH content-farm verdict
should not let a domain read as fully clean).

## New surface area

**Config (`config/settings.py`):**
- `ENABLE_CONTENT_FARM: bool = True`
- `CONTENT_FARM_SAMPLE_ARTICLES: int = 8`
- `CONTENT_FARM_TOP_PAGES: int = 10`        # SEMrush rows when escalated (~100 units)
- `CONTENT_FARM_THIN_WORDS: int = 250`      # secondary thin-content trigger
- `CONTENT_FARM_ESCALATE_TRASH_SHARE: float = 0.4`
- `CONTENT_FARM_ARTICLE_LINK_COUNT: int = 30`   # homepage article-link count → escalate
- `CONTENT_FARM_KEYWORD_FOOTPRINT: int = 5000`

**`AuditResult` field:**
- `content_farm: dict = field(default_factory=dict)` — `{score, band, rationale,
  trivia_share, trash_share, trash_examples, semrush_checked (bool), signals}` — plus
  `to_dict` entries.

**UI (`app.py`):** a content-farm section/tab showing the score + band + rationale,
`trash_share` with example article URLs, and (if run) `trivia_share` with example
phrases; a note when SEMrush was skipped ("content looked legit — SEMrush check
skipped").

**Tests:** unit tests for `extract_internal_article_links` (article vs non-article URL
filtering), the trash/thin per-article rule, and `content_farm_service.compute_signals`
banding/escalation logic. LLM calls themselves are not unit-tested (consistent with the
existing code).

## Cost summary

- Legit domain: 0 SEMrush units, ≤ 8 Bright Data scrapes + 1 LLM call (Check 2 only).
- Suspected farm: + ~100 SEMrush units (top 10, single market) + 1 LLM call (Check 1).
- Disabled entirely via `ENABLE_CONTENT_FARM=false`.

## Open implementation questions (decide during plan)

- Exact LLM prompt wording / output schema for the two judgments and the verdict.
- Whether the two judgment calls (article quality, trivia phrases) can be skipped when
  there are zero article links / zero pages (return empty, no LLM call).

# Spec: PBN / link-scheme detection overhaul + UI wiring

**Date:** 2026-06-14
**Status:** Draft for review

## Background

The auditor currently scores PBN risk from backlink/traffic ratios, topic mismatch,
a raw outbound-domain count (`distinct_external_domains >= 25/15`), domain age, and a
cross-batch shared-IP pass, then an LLM produces the final LOW/MEDIUM/HIGH verdict
(`services/pbn_service.py`, `services/llm_service.py:assess_pbn`).

Problems this spec addresses:

1. The raw outbound-domain count is a poor signal — legitimate sites routinely link
   out (socials, maps, payments, suppliers, credits). Count thresholds either miss
   real link schemes or flag everyone.
2. The real link-scheme tell — **reciprocal linking** between unrelated sites — is not
   detected at all.
3. There is no check for whether the audited site is a **legitimate business**.
4. A homepage that links directly to a known gambling/porn site still runs the entire
   expensive pipeline before being flagged.
5. Two previously-added data fields (`serp_core_results`, `top_countries` /
   `traffic_by_country`) are computed but never shown in the UI.

## Goals

- Stop wasting work on obviously-bad sites (homepage gambling/porn gate, first).
- Replace the raw outbound-count signal with **reciprocity** + **legitimacy**.
- Ignore legitimate/own-entity outbound links entirely (no score effect, no flag).
- Surface the existing core-SERP and markets data in the UI.

## Non-goals (YAGNI)

- No new domain-parsing dependency (`tldextract` etc.). Same-entity detection uses
  cheap built-in mechanisms (below).
- No crawling beyond each strange partner's homepage + its About page.
- No change to the SEMrush/backlinks/SERP data collection itself.

---

## Change 1 — Homepage gambling/porn gate (FIRST step, hard stop)

The gate runs **before anything else** — not in parallel with the data-collection wave.

1. Scrape the homepage (Bright Data) as the first action.
2. Detect gambling/porn outbound links on the homepage via **either**:
   - a `known_bad_sites.txt` match (existing `link_checker.check_links`), **or**
   - the existing AI classifier (`ai_service.classify_outbound_links`) run on the
     homepage's body external links (currently only run on deep pages — move it up).
3. If either fires:
   - set `risk_level = "BAD"` (maps to HIGH in existing bands),
   - record the offending links,
   - set `result.early_failed = True` with a reason,
   - **return immediately**, skipping: the data-collection wave (overview, backlinks,
     SERP x2, network, age), SEMrush per-keyword checks, outbound classification,
     reciprocity, legitimacy, deep-page crawl, and the final AI verdict.

If the gate passes, the rest of the pipeline runs as today (data-collection wave stays
parallel for speed).

**Trade-off accepted:** the good-case path pays one extra serial step (homepage scrape +
one AI classify) before the parallel wave. In exchange, bad sites cost far less than today.

## Change 2 — Outbound link classification (internal / legit / strange)

Operates on the homepage's body external links (`link_checker.extract_body_external_links`).

For each outbound domain, bucket it:

- **Internal / own-entity → ignore entirely** (no score, no flag):
  - same domain or a subdomain of it: `host == domain or host.endswith("." + domain)`
  - other-language variants declared on the homepage via
    `<link rel="alternate" hreflang="..." href="...">` — collect those hosts and treat
    them as own-entity (e.g. `brightdata.es`, `brightdata.de`, `brightdata.jp`).
- **Legit → ignore entirely** (no score, no flag): host matches `data/legit_domains.txt`
  (socials, maps, payment processors, Trustpilot, GitHub, YouTube, common AI/info, agency
  credits). Matching is by registrable host or suffix (`endswith`).
- **Strange → candidate** for the reciprocity check: everything left over. The AI
  classifier may be consulted on leftovers to separate "plainly legit business/utility"
  from "unrelated/strange", reducing reciprocity scrapes.

`result.outbound_classification = {"legit": [...], "strange": [...], "own_entity": [...]}`.
The strange count is informational only (shown, not scored).

## Change 3 — Reciprocity check

For up to `RECIPROCAL_MAX_CHECKS` (default 10) strange domains, gated by
`ENABLE_RECIPROCITY` (default true):

1. Scrape the strange domain's homepage (Bright Data). The homepage HTML already
   contains footer + main navigation, which is where reciprocal links live.
2. Check whether it links **back** to the audited domain (same `host == domain or
   endswith` test, applied to the partner's outbound links).
3. A confirmed link-back = **reciprocal strange link** — a strong link-scheme signal.

`result.reciprocal_links = [{"partner": host, "links_back": bool, "partner_legit": ...}]`
(see Change 4 for `partner_legit`).

## Change 4 — Business-legitimacy check

**Audited site (always):** run cheap heuristics over homepage text **and** About-page text
(the About page is already scraped into `result.about_page_text`):

- phone number present, email present (`mailto:` or text), physical address present,
  personal/business names present, `schema.org` `Organization` / `LocalBusiness` markup,
  presence of a real contact/about page.

The heuristic produces a small structured signal set; the AI (`assess_pbn`) weighs it.
A legitimate business **dampens** PBN risk; a total absence of legitimacy signals **raises** it.

`result.business_legitimacy = {"is_legit": bool, "score": int, "signals": {...}, "ai_reasoning": str}`.

**Reciprocated strange partners only:** for partners where `links_back == True`, also scrape
*their* About page and run the same heuristic legitimacy check, recording `partner_legit`.
A reciprocal partner that is **also not a real business** is the strongest tell.

## Scoring integration (`pbn_service.compute_signals`)

- **Remove** the raw outbound-count rule (current signal #4, `distinct_external_domains
  >= 25/15`).
- **Add** `reciprocal_strange_links` (count + partners) → heavy weight; weight increases
  when a reciprocated partner is itself non-legit.
- **Add** `business_legitimacy` (audited site) → dampener when legit, riser when no signals.
- Keep existing signals: backlinks-without-audience, disproportionate profile,
  sitewide/templated linking, topic mismatch (ratio-based), young-domain, cross-batch IP.
- Legit links, subdomains, and language variants contribute **zero** to the score.

Exact point weights are tunable during implementation; the ordering of strength is:
homepage gambling/porn (auto-BAD) > reciprocal strange link with non-legit partner >
reciprocal strange link > topic mismatch / no-audience backlinks > young domain.

The `assess_pbn` LLM prompt gains the reciprocity evidence and legitimacy findings.

## Change 5 — UI wiring (existing data, currently hidden)

- **Overview tab:** show "Markets checked" from `result.top_countries` and a small
  `traffic_by_country` breakdown.
- **SERP tab:** add a core-business `site:` results block from `result.serp_core_results`
  / `result.serp_core_error`, alongside the existing porn/gambling block.
- Remove the now-dead `bright_data_service.site_search_core_keywords` and the stale
  `result.rankings_error` reference in `app.py`.

## New surface area

**Config (`config/settings.py`):**
- `RECIPROCAL_MAX_CHECKS: int = 10`
- `ENABLE_RECIPROCITY: bool = True`
- `LEGIT_DOMAINS_FILE: Path = DATA_DIR / "legit_domains.txt"`

**Files:**
- `data/legit_domains.txt` — maintainable allowlist of known-good outbound domains.

**`AuditResult` fields (`audit/audit_engine.py`):**
- `early_failed: bool` + `early_fail_reason: Optional[str]`
- `outbound_classification: dict` — `{legit, strange, own_entity}`
- `reciprocal_links: list[dict]`
- `business_legitimacy: dict`

**Permissions:** already auto-approved globally (`~/.claude/settings.json` has
`defaultMode: dontAsk` + a blanket `Bash` allow), so no per-command rules are needed for
the build.

## Cost / performance

- **Bad site (homepage gambling/porn):** short-circuits after 1 scrape + 1 AI call —
  cheaper than today.
- **Good site:** +1 serial homepage scrape/classify before the wave, then up to
  `RECIPROCAL_MAX_CHECKS` partner-homepage scrapes, plus About-page scrapes only for
  reciprocated partners. All bounded.

## Open implementation questions (decide during plan)

- Exact heuristic patterns for address/name detection (locale-agnostic, loose; AI handles nuance).
- Whether the AI leftover-classification in Change 2 is a separate call or folded into the
  existing homepage classify call from Change 1.

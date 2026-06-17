# Spec: Destination-Based Porn/Gamble Detection + Count Threshold

**Date:** 2026-06-17
**Status:** Draft for review

## Background

A real audit (`fenced.ai`, a child-safety company) was wrongly **SKIPped** for
"Linking to porn/gamble websites (13)". Investigation of the audit JSON showed the
flags were overwhelmingly **false positives**:

- The AI link classifier (`classify_outbound_links`) flagged `webroot.com`,
  `123helpme.com`, `statista.com`, `researchgate.net` as **"[AI: adult]"** purely
  because their **URL paths** contain "pornography" — they are a security firm, an
  essay site, a stats site, and an academic paper *about* the topic, not porn sites.
- The keyword heuristic (`gambling_keyword_external_links` / `keyword_links_present`)
  flagged Facebook/LinkedIn/Twitter **share buttons** because the shared article
  *title* ("…snapchat-sexting…") contains "sex".

Only 4 flags were genuine: `bodog.eu`, `gamblizard.com`, `affnook.com`,
`prposting.com/.../guest-post-gambling` — actual gambling/link-scheme sites.

**Root cause:** detection judges the **URL's topic/words** instead of **what the
destination site actually is**. A site that *writes about* porn/gambling
(news/academic/medical/security/safety) is not itself a porn/gambling site.

A secondary problem: **one** confirmed bad link triggers an immediate SKIP, which is
too aggressive.

## Goals

1. Judge the **destination domain's nature**, not the URL's words, so legit sites
   that merely write about the topic are not flagged.
2. Remove the naive keyword heuristic (the share-button / topical-citation noise).
3. Replace "1 bad link → Skip" with a **count threshold**: 3+ distinct confirmed
   porn/gambling **sites** → Skip; 1–2 → Check manually; 0 → continue.

## Non-goals (YAGNI)

- No fetching of destination sites (domain-name + sharper prompt only, per decision).
- No change to PBN / content-farm logic.

---

## 1. AI judges the bare domain (sharper prompt)

**`services/llm_service.py`:**

- **`classify_outbound_links(page_url, external_links)`** currently passes full URLs
  to the LLM and returns flagged URLs. Change it to:
  1. Reduce the input URLs to their **distinct registrable domains**
     (reuse `link_checker._extract_domain`).
  2. Ask the LLM to classify each **domain** (not URL).
  3. Map the verdict back: a URL is flagged iff its domain was judged gambling/adult
     (so the existing return shape — flagged hrefs with `category`/`reason` — is kept,
     and the deep-crawl merge by `found_href` still works).
- **`classify_link_partners(page_url, domains)`** already receives bare domains; only
  its prompt needs the same sharpening.

**Sharper prompt (both functions)** must include explicit guidance:

> Classify the **destination site itself** (its domain), NOT the topic of the URL or
> anchor text. A news, academic, medical, security, government, or online-safety site
> that merely *writes about* gambling or porn is **NOT** a gambling/adult site — do
> NOT flag it. Only flag a domain that **is** a gambling operator / casino / sportsbook
> or an adult/porn site. When unsure, do NOT flag.

Expected effect on the fenced.ai case: webroot/statista/researchgate/independent/
123helpme/clevelandclinic/apa → cleared; bodog/gamblizard/affnook/prposting → kept.

## 2. Remove the naive keyword heuristic

- Delete the use of **`gambling_keyword_external_links`** from:
  - the homepage gate (`_homepage_gambling_gate` step "1b"), and
  - the deep-crawl (`_deep_check_page` `gambling_keyword_links`), and
  - the porn/gamble decision aggregation in `audit_domain` (`_kw_hrefs`).
- Remove the **"N external gambling/adult link(s)"** keyword display driven by
  `keyword_links_present` (`keyword_link_flags` / deep `keyword_flags`) from the
  Links & Page Check tab in `app.py`.
- The function `gambling_keyword_external_links` and the `keyword_links_present`
  display become unused for detection. Keep `gambling_keyword_external_links` defined
  (it has unit tests) but no longer wire it into the decision, OR remove it and its
  test — implementer's choice during the plan; the decision must no longer depend on it.

Confirmed porn/gambling links now come **only** from: known-bad-list matches
(`check_links`) ∪ AI-confirmed domains (`classify_outbound_links` /
`classify_link_partners`).

## 3. Count-based decision (distinct domains)

Define the **confirmed porn/gambling domain set** = distinct registrable domains from:
- homepage `bad_links_found`, plus
- every `deep_page_checks[*].bad_links`

(both already contain known-bad + AI-flagged entries; dedupe by domain via
`_extract_domain`).

Let `N = len(confirmed_pg_domains)`. At the porn/gamble decision step (after the deep
crawl, before PBN/content-farm):

- **N ≥ `PORN_GAMBLE_SKIP_THRESHOLD` (default 3)** → **SKIP**,
  reason `"Linking to {N} porn/gamble sites"`. Short-circuit (no PBN/spam/anchor).
- **N == 1 or 2** → **CHECK_MANUALLY**, reason `"Links to {N} porn/gamble site(s)"`,
  with a flag listing the domains. Short-circuit (no PBN/spam/anchor).
- **N == 0** → continue to PBN/spam.

`steps["porn_gamble_links"]` carries `{status, count, examples}` where `status` is
`"FAIL"` for SKIP, `"WARN"` for the 1–2 manual case, `"PASS"` for 0.

### Homepage gate change

The homepage gate (`_homepage_gambling_gate`) **no longer instant-SKIPs on a single
homepage gambling link**. Instead:
- It still scrapes the homepage and AI-classifies homepage outbound domains, recording
  homepage confirmed-bad domains into `result.bad_links_found`.
- **Fast-path:** if the homepage *alone* already has ≥ `PORN_GAMBLE_SKIP_THRESHOLD`
  confirmed domains, SKIP immediately (preserves the cheap short-circuit for blatant
  cases).
- Otherwise it does NOT return early; the homepage domains feed the unified count
  decided after the deep crawl (above).

The standalone "Failed homepage check" SKIP reason is replaced by the unified
"Linking to N porn/gamble sites" reason. (The "Couldn't fetch homepage" CHECK_MANUALLY
path is unchanged.)

## Config

```python
PORN_GAMBLE_SKIP_THRESHOLD: int = int(os.getenv("PORN_GAMBLE_SKIP_THRESHOLD", "3"))
```

## New surface area

- `services/llm_service.py`: `classify_outbound_links` reworked to domain-level +
  sharper prompt; `classify_link_partners` prompt sharpened.
- `audit/audit_engine.py`: gate no longer instant-skips (fast-path only); deep crawl
  drops `gambling_keyword_links`; porn/gamble decision becomes count-based (skip/manual/
  continue) over distinct confirmed domains; a small pure helper to compute the domain
  set + decision (testable).
- `config/settings.py`: `PORN_GAMBLE_SKIP_THRESHOLD`.
- `app.py`: remove the keyword "external gambling/adult link(s)" display.

## Testing

- Unit test the pure decision helper: 0 domains → continue; 2 → manual; 3 → skip;
  dedupe (`bodog.eu/poker` + `bodog.eu/casino` → 1).
- The AI prompt change and the audit_domain wiring are verified by import + a manual
  re-run against the fenced.ai case (expect: adult false positives gone; the ~4 real
  gambling domains remain → SKIP at the 3+ threshold, which is correct).

## Open implementation questions (decide during plan)

- Whether to fully delete `gambling_keyword_external_links` + its test or just unwire it.
- Exact `status` value ("WARN") for the 1–2 manual case in the phase panel emoji map.

# Spec: Deep-Classifier Allowlist + CHECK_MANUALLY Continues

**Date:** 2026-06-18
**Status:** Draft for review

## Background

Two issues from the latest audit (`fenced.ai`):

1. **False gambling flags on social share buttons.** fenced.ai was SKIPped for
   "Linking to 5 porn/gamble sites", but 2 of the 5 were `facebook.com` and
   `twitter.com` (share-button links), each flagged **`[AI: other_harmful]`**. The
   deep-page AI classifier (`classify_outbound_links`) does NOT apply the
   `data/legit_domains.txt` allowlist, so social/legit domains reach the LLM, which
   mislabeled them; and the porn/gamble **count includes the vague `other_harmful`
   category**, so those false flags inflated the count.
2. **CHECK_MANUALLY short-circuits PBN/spam.** When porn/gamble routes a domain to
   CHECK_MANUALLY (1–2 links, or an "incidental" downgrade), the audit returns early
   and never computes PBN or content-farm — so a human reviewer sees no scores.

## Goals

- Never flag allowlisted/social domains as gambling/adult.
- Only count true gambling/adult categories toward the porn/gamble decision.
- Run PBN + content-farm even when porn/gamble sends a domain to CHECK_MANUALLY, so
  the reviewer has full scores. SKIP still short-circuits.

## Non-goals (YAGNI)

- No change to the destination-domain judgment itself or the count threshold.

---

## Fix 1 — deep classifier: allowlist + count only gambling/adult categories

**1a. Apply the legit-domains allowlist to the deep-page classifier.** In
`audit_domain`'s deep-crawl `_deep_check_page`, before calling
`ai_service.classify_outbound_links`, exclude body external links whose domain is on
the allowlist (`outbound_classifier._load_legit_domains()` /
`outbound_classifier.is_legit_domain`). Re-introduce the one-time legit-list load at
the top of `audit_domain` (it was removed in a prior change):

```python
    from services import outbound_classifier as oc
    oc_legit = oc._load_legit_domains()
```
and filter:
```python
        body_external_links = link_checker.extract_body_external_links(page_html, page_url)
        body_external_links = [
            u for u in body_external_links
            if not oc.is_legit_domain(link_checker._extract_domain(u), oc_legit)
        ]
```

**1b. Only merge gambling/adult categories into `bad_links`.** When merging
`ai_flagged` into `check_entry["bad_links"]`, skip any flag whose `category` is not a
gambling/adult one:

```python
    _PG_CATS = {"gambling", "social_casino", "sports_betting", "adult", "escort"}
    for flagged in ai_flagged:
        if flagged.get("category") not in _PG_CATS:
            continue  # ignore other_harmful / vague flags for the porn/gamble decision
        ...
```

**1c. Drop `other_harmful` from the prompt** of `classify_outbound_links` (the
category enum) so the model isn't invited to use the catch-all. (Belt-and-suspenders
with 1b.)

Effect: facebook.com / twitter.com never reach the classifier (1a) and even if a vague
flag slips through it isn't counted (1b). fenced.ai → 3 real gambling domains.

## Fix 2 — CHECK_MANUALLY continues through PBN + content-farm

Change the porn/gamble decision step in `audit_domain` so **only SKIP short-circuits**;
**CHECK_MANUALLY stashes its verdict and continues**.

Replace the current `if _pg_decision:` build-and-return with:

```python
    _pg_manual = None
    if _pg_decision == "SKIP":
        result.recommendation = {
            "decision": "SKIP", "reason": _pg_reason,
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

(`_pg_manual` must be initialized to `None` and remain in scope down to the headline
builder; if 0 gambling links, it stays `None`.)

Then in the **headline builder**, after `decide_after_scores(...)` produces
`_decision`/`_reason`, if `_pg_manual` is set, override to CHECK_MANUALLY and fold in
the gambling context:

```python
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
```

And set the `porn_gamble_links` step to reflect the manual state (so the phase panel
shows it, and PBN/content_farm now carry real scores):

```python
        "porn_gamble_links": {
            "status": "WARN" if _pg_manual else "PASS",
            "count": _pg_manual["count"] if _pg_manual else 0,
            "examples": _pg_manual["examples"] if _pg_manual else [],
        },
```

Net: a porn/gamble-manual domain runs PBN + content-farm; the verdict stays
CHECK_MANUALLY (gambling reason leads, HIGH PBN/spam appended); the phase panel shows
all four phases with real PBN/spam scores. SKIP unchanged. The
"Couldn't-fetch-homepage/data" CHECK_MANUALLY paths are unchanged (they return before
this, with no data to score).

(The earlier WARN skip-reason in `build_phase_rows` stays — it still applies to the
SKIP case and any path where PBN/spam genuinely didn't run.)

## New surface area

- `services/llm_service.py`: `classify_outbound_links` prompt drops `other_harmful`.
- `audit/audit_engine.py`: re-add `oc_legit` load; allowlist-filter + category-filter
  in the deep crawl; porn/gamble manual no longer returns; headline builder folds in
  `_pg_manual`.
- No new pure helpers strictly required; no config changes.

## Testing

The changes are mostly LLM-prompt + orchestration, not easily unit-tested in isolation.
Verify by:
- `import audit.audit_engine` + full suite stays green.
- Manual re-run against fenced.ai (expect: facebook/twitter gone; 3 gambling domains →
  SKIP-or-manual via the existing promoter check) and against any 1–2-link domain
  (expect: CHECK_MANUALLY with PBN + content-farm scores populated in the panel).

If a small pure helper falls out naturally (e.g. filtering ai_flagged by category),
add a unit test for it.

## Open implementation questions (decide during plan)

- Whether to extract the category filter into a tiny pure, tested helper or inline it.

# Link Partner Auditor – Usage Guide

## Starting the app

```bash
pip install -r requirements.txt
streamlit run app.py
```

Open http://localhost:8501 in your browser.

---

## .env – configuration

Copy `.env.example` to `.env` and fill in the providers you use. Each layer is
swappable with a single env var — no code changes for the built-in options.

| Variable | Notes |
|---|---|
| `SEO_PROVIDER` | `semrush` (default) or `dataforseo` |
| `SEMRUSH_API_KEY` | semrush.com → Account → API (if `SEO_PROVIDER=semrush`) |
| `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` | app.dataforseo.com/api-access (if `SEO_PROVIDER=dataforseo`) |
| `SCRAPER_PROVIDER` | `brightdata` (default) or `requests` (keyless example) |
| `BRIGHTDATA_API_KEY` | brightdata.com/cp/setting/users (only required when `SCRAPER_PROVIDER=brightdata`) |
| `BRIGHTDATA_WEB_UNLOCKER_ZONE` | Default: `web_unlocker1` |
| `BRIGHTDATA_SERP_ZONE` | Default: `serp_api_marketing_make_com` |
| `LLM_PROVIDER` | `openai` (default) or `anthropic` |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | platform.openai.com/api-keys, e.g. `gpt-5.2` |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | console.anthropic.com, e.g. `claude-opus-4-8` |

See `.env.example` for the full list, including throughput tuning and the
recommendation/content-farm thresholds.

---

## Editable lists

One entry per line; lines starting with `#` are comments. After editing, click
**Reload lists** in the app (or restart).

| File | Purpose |
|---|---|
| `keywords/semrush_core_business_keywords.txt` | Your business keywords — SEO rank + position check |
| `keywords/serp_core_business_keywords.txt` | Your business keywords — Google `site:` check |
| `keywords/semrush_porn_gambling_keywords.txt` | Adult/gambling terms — SEO ranking check |
| `keywords/serp_porn_gambling_keywords.txt` | Adult/gambling terms — Google `site:` check |
| `keywords/linkbuilding_targets.txt` | `Keyword - URL` pairs the AI suggests anchors from |
| `data/known_bad_sites.txt` | Known shady domains; powers the homepage hard-fail gate |
| `data/legit_domains.txt` | Known-good outbound domains; ignored by link-scheme scoring |
| `data/competitor_sites.txt` | Your competitor domains (flagged as a non-blocking note) |

---

## How to audit

1. Paste one domain per line (e.g. `example.com`) or upload a CSV with a `url` column.
2. Click **Start Audit**.
3. Review the per-domain verdict; download results as CSV, Excel, or JSON.

---

## What you get

Every domain gets one headline verdict from a short-circuiting decision tree:

| Verdict | Meaning |
|---|---|
| 🔴 **Skip** | Clear reject — failed the homepage gate or links to porn/gambling |
| 🟠 **Check manually** | Ambiguous — couldn't fetch data, or PBN/content-farm came back HIGH |
| 🟢 **Approved** | Passed every gate (the AI also suggests anchor text) |

The checks behind the verdict:

| Check | What it does | Source |
|---|---|---|
| Homepage gate | Is the homepage reachable? | Scraper |
| Niche | Determines the site's topic | LLM |
| Porn/gambling links | Counts distinct adult/gambling **destination** domains (judged by what the domain is, not URL keywords) | Scraper + LLM |
| PBN / link-network | Topic mismatch, reciprocal links, business-legitimacy, shared hosting → 0–100 score | Scraper + SEO |
| Content-farm spam | Thin / AI-generated / low-value article sampling → 0–100 score | Scraper + LLM (+ SEO if escalated) |
| SEO metrics | Authority, organic traffic, backlinks, keyword rankings | SEO provider |
| Competitor flag | Flags the domain if it's in `data/competitor_sites.txt` (non-blocking) | local list |

`risk_level` is kept as a derived value (Skip → HIGH, Check manually → MEDIUM,
Approved → LOW) for backward-compatible exports.

See the [README](README.md) for the full decision tree, thresholds, and how to
add a new SEO / LLM / scraper provider.

---

## Reinstalling dependencies

```bash
pip install -r requirements.txt
```

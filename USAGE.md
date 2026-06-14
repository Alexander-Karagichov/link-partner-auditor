# Website Audit Automation – Usage Guide

## Starting the app

Install dependencies, then run:

```bash
pip install -r requirements.txt
streamlit run app.py
```

Open http://localhost:8501 in your browser.

---

## .env – API Keys

Edit `.env` and fill in:

| Variable | Where to get it |
|---|---|
| `SEO_PROVIDER` | `semrush` (default) or `dataforseo` |
| `SEMRUSH_API_KEY` | semrush.com → Account → API (if `SEO_PROVIDER=semrush`) |
| `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` | app.dataforseo.com/api-access (if `SEO_PROVIDER=dataforseo`) |
| `BRIGHTDATA_API_KEY` | brightdata.com/cp/setting/users |
| `BRIGHTDATA_WEB_UNLOCKER_ZONE` | Default: `web_unlocker1` |
| `BRIGHTDATA_SERP_ZONE` | Default: `serp_api_marketing_make_com` |
| `LLM_PROVIDER` | `openai` (default) or `anthropic` |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | platform.openai.com/api-keys, e.g. `gpt-5.2` |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | console.anthropic.com, e.g. `claude-opus-4-8` |

---

## Keyword files (editable)

| File | Purpose |
|---|---|
| `keywords/semrush_core_business_keywords.txt` | Your business keywords — SEMrush rank + position check |
| `keywords/serp_core_business_keywords.txt` | Your business keywords — Google site: check (Bright Data) |
| `keywords/semrush_porn_gambling_keywords.txt` | Adult/gambling terms to flag |
| `data/known_bad_sites.txt` | Known shady domains to detect as outbound links; powers the homepage hard-fail gate |
| `data/legit_domains.txt` | Known-good outbound domains; ignored by link-scheme scoring |

One entry per line. Lines starting with `#` are comments.

---

## How to audit websites

1. Open the app
2. Paste one domain per line (e.g. `example.com`) or upload a CSV with a `url` column
3. Click **Start Audit**
4. Download results as CSV, Excel, or JSON

---

## What each check does

| Check | Data Source |
|---|---|
| AI Visibility / Mentions / Cited Pages | SEMrush AI Toolkit |
| Authority Score, Organic Traffic | SEMrush Domain Overview |
| Referring Domains, Backlinks | SEMrush Backlinks API |
| Core keyword rankings | SEMrush Organic Research |
| Porn/Gambling keyword rankings | SEMrush Organic Research |
| Bad outbound links on homepage | Bright Data Web Unlocker (`web_unlocker1`) |
| Google `site:` porn/gambling check | Bright Data SERP API (`serp_api_marketing_make_com`) |
| Risk score + summary | OpenAI (`gpt-4o`) |

---

## Reinstalling dependencies

```bash
pip install -r requirements.txt
```

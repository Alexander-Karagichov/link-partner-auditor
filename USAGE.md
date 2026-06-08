# Website Audit Automation – Usage Guide

## Starting the app

Open a terminal in `/workspaces/codespaces-blank` and run:

```bash
streamlit run app.py
```

Then in VS Code:
1. Click the **Ports** tab (bottom panel)
2. Find port **8501** → make sure Visibility is **Public**
3. Click the 🌐 globe icon to open the app in your browser

---

## .env – API Keys

Edit `.env` and fill in:

| Variable | Where to get it |
|---|---|
| `SEMRUSH_API_KEY` | semrush.com → Account → API |
| `BRIGHTDATA_API_KEY` | brightdata.com/cp/setting/users |
| `BRIGHTDATA_WEB_UNLOCKER_ZONE` | Default: `web_unlocker1` |
| `BRIGHTDATA_SERP_ZONE` | Default: `serp_api_marketing_make_com` |
| `OPENAI_API_KEY` | platform.openai.com/api-keys |
| `OPENAI_MODEL` | e.g. `gpt-4o` |

---

## Keyword files (editable)

| File | Purpose |
|---|---|
| `keywords/bright_data_core_keywords.txt` | Bright Data business keywords to flag if competitors rank for them |
| `keywords/porn_gambling_keywords.txt` | Adult/gambling terms to flag |
| `data/known_bad_sites.txt` | Known shady domains to detect as outbound links |

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

# 🔍 Link Partner Auditor

> **Instantly audit any website for brand safety, SEO quality, and link-building potential — before you reach out.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![Powered by Bright Data](https://img.shields.io/badge/Powered%20by-Bright%20Data-orange)](https://brightdata.com/)
[![Streamlit](https://img.shields.io/badge/UI-Streamlit-red)](https://streamlit.io/)

Built with [Bright Data](https://brightdata.com/?utm_source=link-partner-auditor-os) web infrastructure for reliable scraping + SERP data, SEMrush for SEO intelligence, and OpenAI for AI-powered risk scoring.

---

## 😱 The Problem

```
You: *Opens 20 prospect tabs*
You: *Googles "site:domain.com casino" manually*
You: *Checks backlinks in a different tool*
You: *Asks ChatGPT if it looks sketchy*
You: *Creates a spreadsheet and repeat x100*
...
Meanwhile:
  - You missed the site that ranks for 132 porn/gambling keywords
  - You approved a partner whose homepage links to online casinos
  - Your brand safety team asks why you partnered with that domain
```

**Manual link partner vetting is slow, inconsistent, and misses the signals that matter.**

---

## ✨ The Solution

```
Paste 100 domains → Click "Start Audit" → Download results
```

The auditor runs **7 checks in parallel** for every domain:

| Check | What it catches |
|---|---|
| 🔑 SEMrush keyword rankings | Pages ranking for porn/gambling/adult terms |
| 📊 Domain overview | Authority score, organic traffic, backlinks |
| 🌍 Google SERP scan | `site:domain.com` search for each danger term |
| 🔗 Homepage link check | Bad outbound links using known-bad domain list + AI |
| 🔎 Deep page audit | Top-10 flagged pages scraped and checked for bad links |
| 🤖 AI classification | OpenAI classifies external links the list might miss |
| 💡 AI risk summary | GPT-4o assigns a risk level + actionable recommendation |

**Output:**

| Domain | Risk | Niche | Authority | Traffic | Bad Links | Anchor Suggestion |
|---|---|---|---|---|---|---|
| technology.org | 🚨 CRITICAL | Science & tech news | 68 | 93K | 56 | — |
| negup.com | 🔵 LOW | Taxi directory | 28 | 4K | 0 | "web scraping API" |
| droven.io | 🟢 NO_RISK | Food & lifestyle blog | 12 | 7K | 0 | "data pipeline" |

---

## 🚀 Quick Start

### 1. Clone

```bash
git clone https://github.com/Alexanderka47/link-partner-auditor.git
cd link-partner-auditor
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure API keys

```bash
cp .env.example .env
# Edit .env and fill in your keys (see Configuration below)
```

### 4. Run

```bash
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501), paste domains, click **Start Audit**.

---

## ⚙️ Configuration

Copy `.env.example` to `.env` and fill in:

```env
# SEMrush API key — semrush.com → Account → API
SEMRUSH_API_KEY=your_key_here

# Bright Data API key — brightdata.com/cp/setting/users
BRIGHTDATA_API_KEY=your_key_here
BRIGHTDATA_WEB_UNLOCKER_ZONE=web_unlocker1
BRIGHTDATA_SERP_ZONE=serp_api_zone_name

# OpenAI
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-4o

# Tuning
MAX_CONCURRENT_AUDITS=3
REQUEST_TIMEOUT=30
MAX_KEYWORDS_CHECK=50
```

### Keyword files (fully editable)

| File | Purpose |
|---|---|
| `keywords/porn_gambling_keywords.txt` | Terms to flag in SEMrush rankings + SERP |
| `keywords/bright_data_core_keywords.txt` | Your business keywords (competitor detection) |
| `keywords/linkbuilding_targets.txt` | `Keyword - URL` pairs for anchor suggestions |
| `data/known_bad_sites.txt` | Known shady domains to detect as outbound links |
| `data/competitor_sites.txt` | Your competitors' domains |

One entry per line. Lines starting with `#` are comments.

---

## 🔄 Swappable Providers

The codebase is deliberately modular. Each data source lives in its own service file:

```
services/
  semrush_service.py       ← swap for DataForSEO, Ahrefs, Moz, SpyFu
  bright_data_service.py   ← swap for Apify, Oxylabs, ScraperAPI, Playwright
  openai_service.py        ← swap for Anthropic Claude, Google Gemini, Ollama
  link_checker_service.py  ← pure Python, no external dependency
```

### Using DataForSEO instead of SEMrush

Each service exposes a small typed interface. To swap SEMrush for DataForSEO:

1. Create `services/dataforseo_service.py` returning the same dataclasses (`DomainOverview`, `BacklinksOverview`, `OrganicRankings`)
2. Update the import in `audit/audit_engine.py`:
   ```python
   # from services import semrush_service as semrush
   from services import dataforseo_service as semrush
   ```
3. Add your DataForSEO credentials to `.env`

### Using a different LLM

The AI service in `services/openai_service.py` takes plain dicts in and returns plain dicts out. To use Anthropic Claude:

1. Create `services/anthropic_service.py` with the same `analyze_audit()` and `recommend_link_building()` signatures
2. Swap the import in `audit/audit_engine.py`

### Using a different scraper

`services/bright_data_service.py` exposes two functions:
- `scrape_page(url) → (html, error)`
- `serp_search(query, num_results) → list[dict]`

Any scraper that returns those shapes is a drop-in replacement.

---

## 🏗️ Architecture

```
app.py                      ← Streamlit UI
audit/
  audit_engine.py           ← Orchestration: runs all checks, applies risk rules
services/
  semrush_service.py        ← SEO data (domain overview, rankings, backlinks)
  bright_data_service.py    ← Web scraping + SERP via Bright Data
  openai_service.py         ← AI risk scoring + link-building recommendations
  link_checker_service.py   ← HTML parsing, outbound link extraction
config/
  settings.py               ← Env var loading + validation
keywords/                   ← Editable keyword lists
data/                       ← Known bad sites + competitor list
```

### Risk scoring rules (deterministic overrides applied after AI)

| Signal | Risk assigned |
|---|---|
| Homepage links to bad/gambling/adult sites | 🚨 CRITICAL (immediate) |
| No P/G keywords + no confirmed SERP hits + no bad links | 🟢 NO_RISK |
| P/G signals present but zero pages with bad outbound links | 🔵 LOW |
| 1–2 pages with bad links (< 5 total bad links) | 🔵 LOW |
| 3+ pages with bad links | AI score kept (MEDIUM / HIGH / CRITICAL) |

SERP results are **confirmed** only if the matched keyword appears in the URL path — unrelated Google false-positives (e.g. `site:domain.com casino` → `/recipes/`) are filtered out.

---

## 📦 Dependencies

```
streamlit          — UI
requests           — HTTP client
beautifulsoup4     — HTML parsing
lxml               — Fast HTML parser
pandas             — Data tables & export
openpyxl           — Excel export
openai             — OpenAI API client
python-dotenv      — .env loading
```

Install everything with:

```bash
pip install -r requirements.txt
```

---

## 🤝 Contributing

Contributions welcome — especially:
- New provider adapters (DataForSEO, Ahrefs, Oxylabs, Claude, Gemini…)
- Additional risk signals or keyword lists
- Bug fixes and UI improvements

1. Fork the repo
2. Create a feature branch: `git checkout -b feature/dataforseo-adapter`
3. Open a PR with a clear description

---

## 📄 License

MIT — free to use, modify, and distribute. See [LICENSE](LICENSE).

---

> Built with [Bright Data](https://brightdata.com/?utm_source=link-partner-auditor-os) — the world's leading web data infrastructure platform.

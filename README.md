<div align="center">

# 🔍 Link Partner Auditor

**Stop approving sketchy link partners. Audit them in seconds.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue?style=for-the-badge)](https://www.python.org/)
[![Powered by Bright Data](https://img.shields.io/badge/Powered%20by-Bright%20Data-orange?style=for-the-badge)](https://brightdata.com/?utm_source=link-partner-auditor-os)
[![UI: Streamlit](https://img.shields.io/badge/UI-Streamlit-red?style=for-the-badge)](https://streamlit.io/)

Paste a list of domains. Get a **Skip / Check manually / Approved** verdict for each one — brand-safety, SEO quality, PBN signals, and content-farm spam, all in parallel.

[The Problem](#-the-problem) · [The Solution](#-the-solution) · [Quick Start](#-quick-start) · [How It Works](#-how-it-works) · [Configuration](#️-configuration) · [Swappable Providers](#-swappable-providers)

</div>

---

## 😱 The Problem

You're building backlinks. A prospect list lands in your inbox. Now what?

```
You: *Opens 20 prospect tabs*
You: *Googles "site:www.example.com casino" by hand*
You: *Pastes the URL into a backlink tool*
You: *Asks ChatGPT "does this look sketchy?"*
You: *Builds a spreadsheet… then repeats it 100 more times*
...
Meanwhile:
  ✗ You missed the site ranking for 132 porn/gambling keywords
  ✗ You approved a partner whose homepage links to online casinos
  ✗ Your brand-safety team asks why you partnered with *that* domain
```

**Manual link-partner vetting is slow, inconsistent, and misses the exact signals that get you penalized.**

---

## ✨ The Solution

```
Paste 100 domains  →  Click "Start Audit"  →  Download the verdicts
```

For every domain, the auditor runs a battery of checks **in parallel** and short-circuits the moment a domain is a clear reject — so obvious junk costs almost no API quota.

| Check | What it catches |
|---|---|
| 🔑 SEO keyword rankings | Pages ranking for porn / gambling / adult terms |
| 📊 Domain overview | Authority score, organic traffic, backlinks |
| 🌍 Google SERP scan | `site:www.example.com <danger term>` for each domain |
| 🔗 Homepage link check | Bad outbound links — known-bad list **+** AI judgment |
| 🔎 Deep page audit | Top flagged pages scraped and re-checked for bad links |
| 🕵️ PBN / link-network check | Private-blog-network & link-farm patterns |
| 🌾 Content-farm spam score | Thin, low-value, AI-generated trivia content |
| 🤖 AI classification | Classifies outbound links the static list would miss |
| ✅ Verdict | One headline call: **Skip / Check manually / Approved** |

---

## 💡 What You Get

A clean, sortable table you can export to Excel — one row per domain:

| Domain | Recommendation | Spam | Niche | Authority | Traffic | Bad Links |
|---|---|---|---|---|---|---|
| www.example1.com | 🔴 **SKIP** | – | Science & tech news | 68 | 93K | 56 |
| www.example2.com | 🟠 **CHECK MANUALLY** | MEDIUM | Food & lifestyle blog | 12 | 7K | 0 |
| www.example3.com | 🟢 **APPROVED** | LOW | Taxi directory | 28 | 4K | 0 |

- 🔴 **Skip** — clear reject (failed the homepage gate or links to porn/gambling).
- 🟠 **Check manually** — ambiguous signal (couldn't fetch data, or PBN / content-farm came back HIGH).
- 🟢 **Approved** — passed every gate; the AI even suggests anchor text from your link-building targets.

Click any row for the full breakdown: a five-phase panel (Homepage → Niche → P/G links → PBN → Spam), each with an expandable *"why"* rubric.

---

## 🚀 Quick Start

### 1. Clone

```bash
git clone https://github.com/Alexanderka47/link-partner-auditor.git
cd link-partner-auditor
```

### 2. Install

```bash
pip install -r requirements.txt
```

### 3. Configure your API keys

```bash
cp .env.example .env
# Edit .env and fill in your keys (see Configuration below)
```

### 4. Run

```bash
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501), paste your domains, and click **Start Audit**. Done.

---

## 📖 How It Works

The audit scrapes the homepage **first**, then walks a short-circuiting decision tree. It stops at the first failure, so clear rejects spend minimal quota.

```
1. Homepage gate ......... Was the homepage reachable?     → no:  Check manually
2. Data gate ............. Did SEO / keyword data return?  → no:  Check manually
3. Porn / gambling links . How many bad destinations?      → ≥3:  Skip
                                                             1–2: Check manually
4. PBN score ............. Link-network score 70–100?       → yes: Check manually
5. Content-farm score .... Spam score 70–100?              → yes: Check manually
6. Otherwise ............................................. → Approved ✅
```

### 🛡️ Porn / gambling detection — by *what a site is*, not what it says

Outbound links are judged by the **destination domain's nature**, not by URL keywords or anchor text. A news, academic, medical, or online-safety site that merely *writes about* gambling is **not** flagged — only domains that *operate* as adult/gambling services count. The engine resolves each link to its registrable domain, checks `data/known_bad_sites.txt`, and asks the AI to classify the unknowns.

- **Fast-path:** if the homepage alone links to ≥ `PORN_GAMBLE_SKIP_THRESHOLD` (default **3**) distinct bad destinations, the domain is marked **Skip** immediately and the rest of the pipeline is skipped.
- **Promoter vs. incidental:** a genuine gambling **affiliate/promoter** → Skip; a **neutral directory or B2B site** that happens to list a casino company → Check manually.
- **Subdomains excluded:** only the registrable root is audited (Google treats `blog.example.com` as a separate site).
- **Allowlist:** anything in `data/legit_domains.txt` is never flagged — social share buttons (Facebook, X, LinkedIn…) are pre-listed there.

### 🕵️ PBN / link-network check

Flags private-blog-network and link-farm patterns: **topic mismatch** (homepage niche vs. what it ranks for), **backlinks without an audience**, **link-network outbound** behavior, **domain age**, and **shared hosting** across the batch. A **reciprocity check** fetches each *strange* outbound domain and looks for a link back to the audited site — reciprocal links between otherwise-unrelated domains are a strong link-scheme signal. Produces a 0–100 score banded **LOW / MEDIUM / HIGH**.

### 🌾 Content-farm spam score

Cheap first: the LLM samples homepage-linked articles and judges each as low-value trivia (anything under `CONTENT_FARM_THIN_WORDS`, default 250 words, also counts). It **only** escalates to a paid SEO top-pages pull when a site looks suspicious (high trash share, too many article links, or a huge keyword footprint) — so clean sites cost **zero** extra SEO units. Produces a 0–100 score banded **LOW / MEDIUM / HIGH**.

### 🤖 AI analysis details

The LLM does the judgment calls a static list can't: classifying unknown outbound domains, distinguishing promoters from incidental links, rating article quality, determining each site's niche, and writing a plain-language summary of the findings. **`risk_level`** is kept as a derived value (SKIP → HIGH, CHECK_MANUALLY → MEDIUM, APPROVED → LOW) so existing exports keep working.

---

## ⚙️ Configuration

Copy `.env.example` to `.env` and fill in the providers you use. The two provider switches:

```env
# SEO data: "semrush" (default) or "dataforseo"
SEO_PROVIDER=semrush
SEMRUSH_API_KEY=your_key_here
# …or, for DataForSEO:
# SEO_PROVIDER=dataforseo
# DATAFORSEO_LOGIN=your_login
# DATAFORSEO_PASSWORD=your_password

# AI: "openai" (default) or "anthropic"
LLM_PROVIDER=openai
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-5.2
# …or, for Claude:
# LLM_PROVIDER=anthropic
# ANTHROPIC_API_KEY=your_key_here
# ANTHROPIC_MODEL=claude-opus-4-8

# Scraping + SERP (always required) — brightdata.com/cp/setting/users
BRIGHTDATA_API_KEY=your_key_here
BRIGHTDATA_WEB_UNLOCKER_ZONE=web_unlocker1
BRIGHTDATA_SERP_ZONE=serp_api_marketing_make_com
```

See [`.env.example`](.env.example) for the full list, including throughput tuning (`INNER_CONCURRENCY`, `MAX_DEEP_PAGES_PER_DOMAIN`, …).

| Env var | Default | Purpose |
|---|---|---|
| `PORN_GAMBLE_SKIP_THRESHOLD` | `3` | Distinct confirmed adult/gambling destinations that trigger a **Skip**. 1–2 → **Check manually**. |

### Keyword / data files (fully editable)

Each list feeds a different stage — and a different cost. **⚠️ More terms = more API calls per domain.** The lists that hit paid APIs are deliberately separate so you can tune each one independently:

| File | Feeds | Cost per extra term |
|---|---|---|
| `keywords/serp_porn_gambling_keywords.txt` | Google `site:<domain> <term>` via **Bright Data** | 1 Bright Data request/domain (capped by `SERP_MAX_TERMS`, default 10) |
| `keywords/semrush_porn_gambling_keywords.txt` | Danger terms → **SEO** rank check | ~10 units/term, per market |
| `keywords/semrush_core_business_keywords.txt` | Your business keywords → **SEO** rank check | ~10 units/keyword, per market |
| `keywords/serp_core_business_keywords.txt` | Your business keywords → Google `site:` via **Bright Data** | 1 Bright Data request/keyword |
| `keywords/linkbuilding_targets.txt` | `Keyword - URL` pairs the AI picks anchor suggestions from | none |
| `data/known_bad_sites.txt` | Bad outbound domains; powers the homepage hard-fail gate | none (local match) |
| `data/legit_domains.txt` | Known-good domains ignored by link-scheme scoring | none (local match) |
| `data/competitor_sites.txt` | Your competitor domains | none (local match) |

One entry per line; lines starting with `#` are comments. After editing, click **Reload lists** in the app (or restart).

---

## 🔄 Swappable Providers

The tool is **vendor-neutral by design** — no provider is hard-wired. Three dispatchers pick the backend at runtime from an env var:

| Capability | Dispatcher | `env` switch | Built-in options |
|---|---|---|---|
| SEO data | `services/seo_service.py` | `SEO_PROVIDER` | `semrush`, `dataforseo` |
| AI scoring | `services/llm_service.py` | `LLM_PROVIDER` | `openai`, `anthropic` |
| Scraping + SERP | `services/scraper_service.py` | `SCRAPER_PROVIDER` | `brightdata`, `requests` (example) |

Switching is just an env change — `SEO_PROVIDER=dataforseo`, `LLM_PROVIDER=anthropic`, `SCRAPER_PROVIDER=requests` — then restart. **No code edits.**

<details>
<summary><strong>Adding a new SEO provider (Ahrefs, Moz, SpyFu…)</strong></summary>

1. Create `services/<name>_service.py` implementing the four functions in the shared interface (`services/seo_models.py`), returning the shared dataclasses (`DomainOverview`, `BacklinksOverview`, `OrganicRankings`, `OrganicKeyword`):
   ```python
   get_domain_overview(domain)                   -> DomainOverview
   get_backlinks_overview(domain)                -> BacklinksOverview
   get_organic_rankings(domain, limit)           -> OrganicRankings
   get_organic_keywords_for_terms(domain, terms) -> list[OrganicKeyword]
   ```
2. Register it in `services/seo_service.py` under a new `SEO_PROVIDER` value.

`services/dataforseo_service.py` is a complete worked example.
</details>

<details>
<summary><strong>Adding a new LLM (Gemini, Ollama…)</strong></summary>

Implement `chat_json(system_prompt, user_prompt, max_tokens) -> str` in `services/<name>_service.py` and register it in `services/llm_service.py` under a new `LLM_PROVIDER` value. `services/anthropic_service.py` is the example.
</details>

<details>
<summary><strong>Adding a new scraper (ScraperAPI, Zyte, Oxylabs, Playwright…)</strong></summary>

A scraper backend implements just **two primitives**:

```python
scrape_page(url)                -> (html, error)
serp_search(query, num_results) -> (list[dict], error)   # dicts: position, title, url, snippet
```

1. Create `services/<name>_scraper_service.py` with those two functions. `services/requests_scraper_service.py` is a complete worked example.
2. Register it in `services/scraper_service.py` under a new `SCRAPER_PROVIDER` value.

Everything else (the `site:<domain> <term>` searches, term loading, domain filtering) lives in `scraper_service.py` and is provider-agnostic, so you never reimplement it. Set `SCRAPER_PROVIDER=<name>` and restart.
</details>

---

## 🎯 Use Cases

- **Link builders & SEO agencies** — vet outreach lists before you spend a single email.
- **Brand-safety teams** — prove a partner doesn't link to porn/gambling before you sign.
- **Marketplace / directory operators** — screen sites applying to join your network.
- **Anyone buying or auditing backlinks** — catch PBNs and content farms before Google does.

---

## 🏗️ Architecture

```
app.py                      ← Streamlit UI
audit/
  audit_engine.py           ← Orchestration: runs all checks, applies the decision tree
services/
  seo_service.py            ← SEO dispatcher (semrush / dataforseo)
  llm_service.py            ← AI dispatcher (openai / anthropic)
  scraper_service.py        ← Scraper dispatcher (brightdata / requests) + site: search logic
  bright_data_service.py    ← Default scraper backend (Web Unlocker + SERP)
  link_checker_service.py   ← HTML parsing, outbound link extraction
  pbn_service.py            ← PBN / link-network scoring
  content_farm_service.py   ← Content-farm spam scoring
  recommendation_service.py ← Skip / Check manually / Approved decision tree
config/
  settings.py               ← Env var loading + validation
keywords/                   ← Editable keyword lists
data/                       ← Known-bad sites, legit allowlist, competitor list
```

---

## ⚠️ Limitations

- **It costs API quota.** Bright Data, SEMrush/DataForSEO, and OpenAI/Anthropic all bill per call — the short-circuiting tree keeps it low, but a 1,000-domain run is not free.
- **AI judgment isn't infallible.** *Check manually* exists on purpose; treat the verdict as a strong first pass, not a final ruling.
- **SEO data is provider-bound.** Coverage and accuracy depend on whichever SEO provider you wire in.
- **Subdomains are intentionally skipped.** Only registrable roots are audited.

---

## 🤝 Contributing

Contributions welcome — especially:

- New provider adapters (Ahrefs, Oxylabs, Gemini, Ollama…)
- Additional risk signals or keyword lists
- Bug fixes and UI improvements

1. Fork the repo
2. Branch: `git checkout -b feature/ahrefs-adapter`
3. Open a PR with a clear description

---

## 👤 Author

**Alexander Karagichev**

- GitHub: [@Alexander-Karagichov](https://github.com/Alexander-Karagichov)

---

## 📄 License

MIT — free to use, modify, and distribute. See [LICENSE](LICENSE).

---

<div align="center">

### 🔍 Vet Before You Link

*"One bad backlink can undo a year of clean SEO."*

Stop guessing which partners are safe. Start knowing.

```bash
streamlit run app.py
```

Built with [Bright Data](https://brightdata.com/?utm_source=link-partner-auditor-os) — the world's leading web data infrastructure platform.

</div>

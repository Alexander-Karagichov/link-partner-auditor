# 🔍 Link Partner Auditor

> **Instantly audit any website for brand safety, SEO quality, and link-building potential — before you reach out.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![Powered by Bright Data](https://img.shields.io/badge/Powered%20by-Bright%20Data-orange)](https://brightdata.com/)
[![Streamlit](https://img.shields.io/badge/UI-Streamlit-red)](https://streamlit.io/)

Built with [Bright Data](https://brightdata.com/?utm_source=link-partner-auditor-os) for reliable scraping + SERP data, a **swappable SEO provider** (SEMrush **or** DataForSEO), and a **swappable LLM** (OpenAI **or** Anthropic Claude) for AI-powered risk scoring. Pick your providers with two env vars — no code changes.

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
| 💡 AI summary | The LLM writes a plain-language summary of the findings |
| ✅ Verdict | A short-circuiting decision tree yields **Skip / Check manually / Approved** |
| 🕵️ PBN / link-network check | Flags private-blog-network / link-farm patterns: topic mismatch (homepage vs. rankings), backlinks-without-audience, link-network outbound, domain age, and shared hosting across the batch |

**Output:**

| Domain | Recommendation | Spam Score | Niche | Authority | Traffic | Bad Links | Anchor |
|---|---|---|---|---|---|---|---|
| technology.org | 🔴 SKIP | – | Science & tech news | 68 | 93K | 56 | – |
| negup.com | 🟢 APPROVED | LOW | Taxi directory | 28 | 4K | 0 | "web scraping API" |
| droven.io | 🟠 CHECK MANUALLY | MEDIUM | Food & lifestyle blog | 12 | 7K | 0 | – |

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

Copy `.env.example` to `.env` and fill in the providers you use. The two
provider switches are:

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

See [`.env.example`](.env.example) for the full list, including throughput
tuning (`INNER_CONCURRENCY`, `MAX_DEEP_PAGES_PER_DOMAIN`, …).

| Env var | Default | Purpose |
|---|---|---|
| `PORN_GAMBLE_SKIP_THRESHOLD` | `3` | Minimum number of distinct confirmed adult/gambling destination domains that triggers a **Skip**. 1–2 destinations yield **Check manually** instead. |

### Keyword / data files (fully editable)

Each list feeds a different stage of the audit — and a different cost.
**⚠️ More terms = more API calls per domain.** The two lists below that hit paid
APIs are deliberately separate so you can tune each independently:

| File | Feeds | Cost per extra term |
|---|---|---|
| `keywords/serp_porn_gambling_keywords.txt` | Google `site:<domain> <term>` checks via **Bright Data** | **1 Bright Data request per domain** (hard-capped by `SERP_MAX_TERMS`, default 10) |
| `keywords/semrush_porn_gambling_keywords.txt` | Danger terms → **SEMrush** rank + position check | ~10 units per term, per market |
| `keywords/semrush_core_business_keywords.txt` | Your business keywords → **SEMrush** rank + position check | ~10 units per keyword, per market |
| `keywords/serp_core_business_keywords.txt` | Your business keywords → Google `site:` check via **Bright Data** | 1 Bright Data request per keyword (capped by `SERP_MAX_TERMS`) |
| `keywords/linkbuilding_targets.txt` | `Keyword - URL` pairs the AI picks anchor suggestions from | none |
| `data/known_bad_sites.txt` | Domains flagged as bad outbound links; also powers the homepage hard-fail gate | none (local match) |
| `data/legit_domains.txt` | Known-good outbound domains ignored by link-scheme scoring | none (local match) |
| `data/competitor_sites.txt` | Your competitor domains | none (local match) |

One entry per line; lines starting with `#` are comments. After editing, click
**Reload lists** in the app (or restart). `serp_porn_gambling_keywords.txt` and
`semrush_porn_gambling_keywords.txt` are **separate on purpose** — one drives the Bright
Data Google checks, the other the SEMrush ranking checks.

---

## 🔄 Swappable Providers

The tool is vendor-neutral by design — **no provider is hard-wired.** Two
dispatchers pick the backend at runtime from an env var:

| Capability | Dispatcher | `env` switch | Built-in options |
|---|---|---|---|
| SEO data | `services/seo_service.py` | `SEO_PROVIDER` | `semrush`, `dataforseo` |
| AI scoring | `services/llm_service.py` | `LLM_PROVIDER` | `openai`, `anthropic` |
| Scraping + SERP | `services/bright_data_service.py` | — | Bright Data |

Switching is just an env change — e.g. `SEO_PROVIDER=dataforseo` or
`LLM_PROVIDER=anthropic` — then restart. No code edits.

### Adding a new SEO provider (Ahrefs, Moz, SpyFu, …)

1. Create `services/<name>_service.py` implementing the four functions in the
   shared interface (`services/seo_models.py`), returning the shared dataclasses
   (`DomainOverview`, `BacklinksOverview`, `OrganicRankings`, `OrganicKeyword`):
   ```python
   get_domain_overview(domain)             -> DomainOverview
   get_backlinks_overview(domain)          -> BacklinksOverview
   get_organic_rankings(domain, limit)     -> OrganicRankings
   get_organic_keywords_for_terms(domain, terms) -> list[OrganicKeyword]
   ```
2. Register it in `services/seo_service.py` under a new `SEO_PROVIDER` value.

`services/dataforseo_service.py` is a complete worked example.

### Adding a new LLM (Gemini, Ollama, …)

Implement `chat_json(system_prompt, user_prompt, max_tokens) -> str` in
`services/<name>_service.py` and register it in `services/llm_service.py` under
a new `LLM_PROVIDER` value. (`services/anthropic_service.py` is the example.)

### Using a different scraper

`services/bright_data_service.py` exposes `scrape_page(url) → (html, error)` and
`serp_search(query, num_results) → list[dict]`. Any scraper returning those
shapes is a drop-in replacement.

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

### Final verdict

The old rule-based risk-level overrides (CRITICAL / NO_RISK / LOW …) have been **replaced**
by the short-circuiting **Recommendation** engine (see below), which produces a single
**Skip / Check manually / Approved** verdict. `risk_level` is kept only as a derived value
(SKIP → HIGH, CHECK_MANUALLY → MEDIUM, APPROVED → LOW) so existing exports keep working.

SERP results are **confirmed** only if the matched keyword appears in the URL path — unrelated Google false-positives (e.g. `site:domain.com casino` → `/recipes/`) are filtered out.

### Homepage gambling/adult hard-fail gate

The audit scrapes the homepage **first**. Outbound links are judged by the **destination domain's nature** — not by URL keywords or anchor text. A news, academic, medical, or online-safety site that merely *writes about* porn or gambling is not flagged; only domains that *operate* as adult/gambling services are counted. The engine resolves each link to its registrable domain, checks it against `data/known_bad_sites.txt`, and (for unknowns) asks the AI to classify it.

**Fast-path:** if the homepage alone already links to `PORN_GAMBLE_SKIP_THRESHOLD` or more distinct confirmed bad domains (default **3**), the domain is immediately marked **Skip** and all remaining checks are skipped, saving API quota for clear-cut rejects.

### Reciprocity check (PBN / link-scheme signal)

Outbound links found on the audited site are classified into three buckets:

- **own-entity** — same registrable domain as the audited site
- **allowlisted-legit** — present in `data/legit_domains.txt`
- **strange** — everything else

For each *strange* domain (up to `RECIPROCAL_MAX_CHECKS`, default 10), the engine fetches that page and checks whether it links back to the audited site. A reciprocal link between two otherwise unrelated domains is a strong PBN/link-scheme signal and raises the risk score. Set `ENABLE_RECIPROCITY=false` to disable this step.

### Business-legitimacy check

The audited site (and any reciprocating partner pages) is scanned for standard legitimacy signals: contact email, phone number, physical address, schema.org `Organization` / `LocalBusiness` markup, and the presence of `/contact` or `/about` pages. Missing several of these signals contributes to a higher risk score.

### Content-farm spam score

Runs after the homepage is scraped; skipped entirely if the domain already hard-failed the gambling/porn gate.

**Cheap first — LLM article sampling.** The engine samples up to `CONTENT_FARM_SAMPLE_ARTICLES` (default 8) homepage-linked internal articles. An LLM judges each one as low-value trivia / SEO-bait. An article also counts as trash if it is under `CONTENT_FARM_THIN_WORDS` (default 250) words. Service pages, product pages, and landing pages are **not** treated as trivia — only genuine low-value informational articles qualify.

**Escalates to a paid SEMrush pull only if suspicious.** A SEMrush top-pages pull is triggered when any of the following are true:

- The trash share of sampled articles exceeds `CONTENT_FARM_ESCALATE_TRASH_SHARE` (default 0.4, i.e. 40 %)
- The homepage links to more than `CONTENT_FARM_ARTICLE_LINK_COUNT` (default 30) internal articles
- The domain's organic keyword footprint exceeds `CONTENT_FARM_KEYWORD_FOOTPRINT` (default 5 000 keywords)

The SEMrush pull fetches the top `CONTENT_FARM_TOP_PAGES` (default 10) pages by traffic for the single #1 market (~10 API units/row). An LLM then rates how many of those pages target trivia / low-intent queries. Clean sites spend **0 SEMrush units** on this check.

**Output.** A 0–100 content-farm score is produced, banded as **LOW / MEDIUM / HIGH** and shown as its own result banner. A HIGH verdict nudges the overall domain risk upward. Set `ENABLE_CONTENT_FARM=false` to skip the check entirely.

### Recommendation (Skip / Check manually / Approved)

After all checks complete, the audit produces a single headline recommendation via a short-circuiting decision tree. It stops at the first failure, so clear rejects spend minimal API quota.

**Decision tree (evaluated in order):**

1. **Homepage gate** — was the homepage reachable?  If not → **Check manually** (couldn't fetch).
2. **Data gate** — did SEO/keyword data come back?  If not → **Check manually** (couldn't fetch data).
3. **Porn/gambling outbound links** — across the homepage and audited pages, how many distinct confirmed adult/gambling destination domains are linked? Destinations are judged by **what the site is**, not by URL words or anchor text (eliminating false positives on topical citations and share buttons). **≥ `PORN_GAMBLE_SKIP_THRESHOLD` (default 3) distinct bad destinations → Skip**. **1–2 bad destinations → Check manually** (the flag lists them). **0 → continue**.
4. **PBN score HIGH** — PBN/link-farm score 70–100?  If yes → **Check manually** (reason includes the 0–100 score).
5. **Content-farm score HIGH** — content-farm score 70–100?  If yes → **Check manually** (reason includes the score).
6. Otherwise → **Approved**.

**Outcomes:**

| Decision | Meaning | Anchor/link suggestion generated? |
|---|---|---|
| **Skip** | Clear reject — failed homepage gate or links to porn/gambling | No |
| **Check manually** | Ambiguous signal — couldn't fetch data, or PBN/content-farm came back HIGH | No |
| **Approved** | Passed every gate | Yes |

**Porn/gambling detection refinements:**

- **Subdomains excluded from the deep crawl.** Only the registrable root domain is audited; subdomains (e.g. `china.xavor.com`) are skipped. Google treats subdomains as separate sites, so gambling content hosted on a subdomain does not fail the main domain.
- **Promoter vs. incidental.** When a site would otherwise be flagged for linking to gambling sites, the AI distinguishes a genuine gambling **promoter / affiliate** (→ Skip) from a **neutral directory, news, or B2B site** that links to gambling companies incidentally — e.g. a business-directory whose company profiles happen to include casino or lottery operators (→ Check manually).
- **Allowlisted / social domains are never flagged.** The deep link classifier skips any destination domain listed in `data/legit_domains.txt`. Social share buttons (Facebook, Twitter/X, LinkedIn, etc.) are pre-listed there, so they never contribute to the bad-link count regardless of the page topic.
- **Check-manually still runs PBN and content-farm checks.** Receiving a **Check manually** verdict (e.g. from 1–2 bad outbound links or a failed fetch) does **not** short-circuit the pipeline — PBN and content-farm scoring continue so reviewers see the full picture. Only **Skip** halts remaining checks. If PBN or content-farm comes back HIGH on a domain that reached this stage, that finding is appended to the verdict reason.

**Flags (shown, non-blocking — do not change the decision):**

- **Is a Competitor?** — the audited site is flagged as a competitor **only if it appears in `data/competitor_sites.txt`** (the list you maintain). Purely informational — shown in the **"Is a Competitor?"** column and noted in the verdict reason; it does **not** change the Skip / Check manually / Approved decision.
- Links to a competitor domain (`data/competitor_sites.txt`)
- New domain (< `RECO_YOUNG_DOMAIN_DAYS` days, default 180) **and** low organic traffic (< `RECO_LOW_TRAFFIC` visits/mo, default 1 000)
- PBN score **MEDIUM** (40–69)
- Content-farm score **MEDIUM** (40–69)

**What is always shown:** every step's gate result — homepage PASS/FAIL, porn/gambling links PASS/FAIL + count, PBN 0–100 + band, content-farm 0–100 + band.

**Backward compatibility:** `risk_level` is derived from the decision: SKIP → HIGH, CHECK_MANUALLY → MEDIUM, APPROVED → LOW.

### Detailed results panel

The per-domain results view is broken into **five sequential phases**, each shown as its own collapsible panel:

| Phase | What it shows |
|---|---|
| **Homepage gate** | Reachability pass/fail |
| **Niche** | Site topic determined right after the homepage gate passes — visible even on SKIP results |
| **P/G links** | Porn/gambling outbound-link verdict + count |
| **PBN** | 0–100 score + band; expandable "Why" rubric listing the key signals and band thresholds |
| **Spam / content-farm** | 0–100 score + band; same expandable "Why" rubric |

Phases that were skipped because an earlier one failed are labelled with the reason (e.g. *"Skipped — failed P/G links check"*) rather than left blank.

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
anthropic          — Anthropic Claude API client
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

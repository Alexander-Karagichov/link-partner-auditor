# Content-Farm Spam Score Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a standalone content-farm spam score that judges a site's homepage articles (cheap) and, only when suspicious, its top-traffic SEMrush pages (conditional), producing a 0–100 score + LOW/MEDIUM/HIGH verdict shown like the PBN score.

**Architecture:** Pure, unit-tested helpers (`extract_internal_article_links`, `content_farm_service`) plus three LLM judgments in `llm_service`. `audit_domain` runs a cheap homepage-article check first and escalates to a bounded SEMrush top-pages pull only when escalation triggers fire. Mirrors the existing PBN feature's shape (signals + LLM verdict + band-floor reconciliation).

**Tech Stack:** Python 3, BeautifulSoup (lxml), Streamlit, SEMrush API, OpenAI/Anthropic via `_backend.chat_json`. Tests via `pytest` (already set up).

**Spec:** `docs/superpowers/specs/2026-06-14-content-farm-spam-score-design.md`

**Conventions to follow (verified in the codebase):**
- Run tests with the project venv: `.venv/Scripts/python.exe -m pytest ...`. Do NOT `pip install`.
- Use the **Bash** tool for shell commands (the PowerShell tool is fine too now, but Bash is simplest).
- `link_checker_service._extract_domain(href)` → lowercase netloc (strips a leading `www.` via `removeprefix`). `_is_nav_or_footer(tag)` → True inside nav/header/footer. `extract_page_text(html)` → returns a string whose first line is `Title: ...`. Reuse these.
- SEMrush helpers in `semrush_service.py`: `_clean_domain`, `_api_key`, `_get`, `_parse_tabular_response`, `_safe_int`, `OrganicKeyword`, `logger`, `settings`. The existing `_rankings_one_db` shows the exact request pattern.
- LLM calls use `_backend.chat_json(system, prompt, max_tokens=...)` + `_parse_json(raw)` (returns a dict). `json` and `logger` are imported in `llm_service.py`.
- `audit_engine` aliases: `from services import seo_service as semrush`, `bright_data_service as bdata`, `link_checker_service as link_checker`, `llm_service as ai_service`. `ThreadPoolExecutor`, `Optional`, `settings`, `logger` are imported.

---

## File Structure

**Create:**
- `services/content_farm_service.py` — pure scoring/escalation/article-evaluation logic
- `tests/test_content_farm_service.py`
- `tests/test_article_links.py`

**Modify:**
- `services/link_checker_service.py` — add `extract_internal_article_links`
- `services/semrush_service.py` — add `get_top_traffic_pages`
- `services/dataforseo_service.py` — add `get_top_traffic_pages` parity stub (returns `[]`)
- `services/seo_service.py` — re-export `get_top_traffic_pages`
- `services/llm_service.py` — add `classify_trivia_phrases`, `classify_article_quality`, `assess_content_farm`
- `config/settings.py` — content-farm config knobs
- `audit/audit_engine.py` — `AuditResult.content_farm` field + `to_dict` + orchestration + risk nudge
- `app.py` — content-farm banner (mirrors PBN banner)
- `README.md`, `.env.example` — docs

---

## Task 1: Homepage article-link extractor

**Files:**
- Modify: `services/link_checker_service.py` (add after `extract_all_external_links`)
- Test: `tests/test_article_links.py`

- [ ] **Step 1: Write the failing test** — create `tests/test_article_links.py`:

```python
from services import link_checker_service as lc


def test_extracts_article_links_excludes_nav_footer_and_nonarticles():
    html = """
    <html><body>
      <nav><a href="/about">About</a></nav>
      <main>
        <a href="/how-many-seconds-in-a-day/">art1</a>
        <a href="https://example.com/another-trivia-post/">art2</a>
        <a href="/category/news/">cat</a>
        <a href="/contact">contact</a>
        <a href="https://other.com/external-post/">ext</a>
        <a href="/image.jpg">img</a>
        <a href="/">home</a>
      </main>
      <footer><a href="/footer-article-here/">f</a></footer>
    </body></html>
    """
    links = lc.extract_internal_article_links(html, "https://example.com")
    assert any(l.endswith("/how-many-seconds-in-a-day/") for l in links)
    assert any("another-trivia-post" in l for l in links)
    assert not any("/category/" in l for l in links)     # section index excluded
    assert not any("contact" in l for l in links)        # non-article excluded
    assert not any("other.com" in l for l in links)      # external excluded
    assert not any("image.jpg" in l for l in links)      # asset excluded
    assert not any("footer-article" in l for l in links) # footer excluded


def test_returns_empty_on_unparseable():
    assert lc.extract_internal_article_links("", "https://example.com") == []
```

- [ ] **Step 2: Run it, verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_article_links.py -v`
Expected: FAIL — `extract_internal_article_links` not defined.

- [ ] **Step 3: Implement** — add to `services/link_checker_service.py` after `extract_all_external_links`:

```python
# Homepage path segments that are section indexes / utility pages, not articles.
_NON_ARTICLE_SEGMENTS = {
    "category", "categories", "tag", "tags", "author", "page", "search", "feed",
    "contact", "about", "privacy", "terms", "cart", "checkout", "account", "login",
    "register", "shop", "product", "products", "wp-admin", "wp-login",
}
_ASSET_RE = re.compile(r"\.(jpg|jpeg|png|gif|svg|webp|pdf|zip|mp4|css|js)(\?|$)", re.IGNORECASE)


def extract_internal_article_links(html: str, base_url: str) -> list[str]:
    """
    Return internal, article-like links from the homepage BODY (nav/header/footer
    excluded). 'Article-like' = an internal path whose first segment isn't a known
    section/utility word, isn't an asset file, and whose last segment looks like a
    slug (has a hyphen, or is reasonably long). Used by the content-farm check —
    a homepage packed with these is a content-hub tell. Deduplicated, order kept.
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return []

    source_domain = _extract_domain(base_url)
    seen: set[str] = set()
    out: list[str] = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        if _is_nav_or_footer(tag):
            continue
        absolute = urljoin(base_url, href)
        d = _extract_domain(absolute)
        if not d or not (d == source_domain or d.endswith("." + source_domain)):
            continue  # internal only
        path = urlparse(absolute).path.strip("/").lower()
        if not path:
            continue  # homepage itself
        segments = [s for s in path.split("/") if s]
        if segments[0] in _NON_ARTICLE_SEGMENTS:
            continue
        if _ASSET_RE.search(path):
            continue
        slug = segments[-1]
        if "-" not in slug and len(slug) < 8:
            continue  # short single-word path → likely a section, not an article
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append(absolute)
    return out
```

- [ ] **Step 4: Run it, verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_article_links.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add services/link_checker_service.py tests/test_article_links.py
git commit -m "feat: homepage article-link extractor for content-farm check"
```

---

## Task 2: content_farm_service (pure scoring)

**Files:**
- Create: `services/content_farm_service.py`
- Test: `tests/test_content_farm_service.py`

- [ ] **Step 1: Write the failing tests** — create `tests/test_content_farm_service.py`:

```python
from services import content_farm_service as cf


def test_evaluate_articles_trivia_or_thin():
    arts = [
        {"url": "a", "is_trivia": True, "word_count": 900},   # trivia
        {"url": "b", "is_trivia": False, "word_count": 120},  # thin
        {"url": "c", "is_trivia": False, "word_count": 800},  # ok
    ]
    out = cf.evaluate_articles(arts, thin_words=250)
    assert out["judged"] == 3
    assert abs(out["trash_share"] - 2 / 3) < 1e-6
    assert "a" in out["trash_examples"] and "b" in out["trash_examples"]


def test_evaluate_articles_empty():
    assert cf.evaluate_articles([], thin_words=250) == {
        "trash_share": 0.0, "trash_examples": [], "judged": 0,
    }


def test_should_escalate_each_trigger():
    kw = dict(trash_threshold=0.4, link_threshold=30, footprint_threshold=5000)
    assert cf.should_escalate(0.5, 0, 0, **kw) is True       # trash share
    assert cf.should_escalate(0.0, 40, 0, **kw) is True      # article links
    assert cf.should_escalate(0.0, 0, 9000, **kw) is True    # keyword footprint
    assert cf.should_escalate(0.1, 5, 100, **kw) is False    # none


def test_compute_signals_high_when_farmy():
    out = cf.compute_signals(
        trivia_share=0.8, trash_share=0.75, judged_articles=8,
        article_link_count=40, keyword_footprint=9000, semrush_checked=True,
    )
    assert out["band"] == "HIGH"
    assert out["score"] >= 55
    assert out["signals"]["semrush_checked"] is True


def test_compute_signals_low_when_clean():
    out = cf.compute_signals(
        trivia_share=None, trash_share=0.0, judged_articles=6,
        article_link_count=4, keyword_footprint=100, semrush_checked=False,
    )
    assert out["band"] == "LOW"
    assert out["score"] == 0
```

- [ ] **Step 2: Run them, verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_content_farm_service.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** — create `services/content_farm_service.py`:

```python
"""
Content-farm spam scoring (pure logic; no I/O).

Combines two checks into a 0-100 score + LOW/MEDIUM/HIGH band:
  - Check 2: share of sampled homepage articles that are trivia/low-value or thin
  - Check 1 (conditional): share of top-traffic SEMrush pages that are trivia queries
plus minor structural nudges (homepage article-link count, keyword footprint).

The audit engine fetches/judges; this module only scores. The LLM produces the
final verdict (llm_service.assess_content_farm); this score is the heuristic prior.
"""
from __future__ import annotations

from typing import Optional


def evaluate_articles(articles: list[dict], thin_words: int) -> dict:
    """
    `articles`: [{url, is_trivia: bool, word_count: int}]. An article is trash if
    the LLM judged it trivia OR its body word count is below `thin_words`.
    Returns {trash_share, trash_examples, judged}.
    """
    judged = len(articles)
    if not judged:
        return {"trash_share": 0.0, "trash_examples": [], "judged": 0}
    trash = [
        a for a in articles
        if a.get("is_trivia") or (a.get("word_count", 10**9) < thin_words)
    ]
    return {
        "trash_share": len(trash) / judged,
        "trash_examples": [a.get("url", "") for a in trash][:5],
        "judged": judged,
    }


def should_escalate(trash_share: float, article_link_count: int, keyword_footprint: int,
                    *, trash_threshold: float, link_threshold: int,
                    footprint_threshold: int) -> bool:
    """Decide whether to spend SEMrush units on Check 1."""
    return (
        trash_share >= trash_threshold
        or article_link_count >= link_threshold
        or (keyword_footprint or 0) >= footprint_threshold
    )


def compute_signals(*, trivia_share: Optional[float], trash_share: float,
                    judged_articles: int, article_link_count: int,
                    keyword_footprint: int, semrush_checked: bool) -> dict:
    """Heuristic 0-100 content-farm score + band + reasons. Weights are tunable."""
    score = 0
    reasons: list[str] = []

    if judged_articles:
        if trash_share >= 0.6:
            score += 40
            reasons.append(f"{trash_share * 100:.0f}% of sampled homepage articles are trivia/low-value filler.")
        elif trash_share >= 0.3:
            score += 22
            reasons.append(f"{trash_share * 100:.0f}% of sampled homepage articles are trivia/low-value.")

    if article_link_count >= 30:
        score += 12
        reasons.append(f"Homepage links to {article_link_count} internal articles — content-hub structure.")

    if semrush_checked and trivia_share is not None:
        if trivia_share >= 0.5:
            score += 35
            reasons.append(f"{trivia_share * 100:.0f}% of top-traffic pages are trivia queries — earns traffic for junk.")
        elif trivia_share >= 0.25:
            score += 18
            reasons.append(f"{trivia_share * 100:.0f}% of top-traffic pages are trivia queries.")

    if (keyword_footprint or 0) >= 5000:
        score += 6
        reasons.append(f"Large keyword footprint ({keyword_footprint}) — consistent with mass-produced content.")

    score = min(score, 100)
    band = "HIGH" if score >= 55 else "MEDIUM" if score >= 25 else "LOW"
    signals = {
        "trivia_share": trivia_share,
        "trash_share": round(trash_share, 3),
        "judged_articles": judged_articles,
        "article_link_count": article_link_count,
        "keyword_footprint": keyword_footprint,
        "semrush_checked": semrush_checked,
    }
    return {"score": score, "band": band, "signals": signals, "reasons": reasons}
```

- [ ] **Step 4: Run them, verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_content_farm_service.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add services/content_farm_service.py tests/test_content_farm_service.py
git commit -m "feat: content-farm scoring service (pure logic)"
```

---

## Task 3: SEMrush top-traffic-pages pull (through the provider seam)

**Files:**
- Modify: `services/semrush_service.py` (real impl)
- Modify: `services/dataforseo_service.py` (parity stub → `[]`)
- Modify: `services/seo_service.py` (re-export)

- [ ] **Step 1: Implement in `services/semrush_service.py`** — add after `get_organic_rankings` (the `_rankings_one_db` function shows the exact request shape to mirror):

```python
def get_top_traffic_pages(domain: str, database: str = "us", limit: int = 10) -> list[OrganicKeyword]:
    """
    Fetch the domain's top organic results sorted by TRAFFIC (descending) for ONE
    database. Used by the content-farm trivia check to see what queries actually
    drive the site's traffic. ~10 API units per row; keep `limit` small.
    """
    domain = _clean_domain(domain)
    out: list[OrganicKeyword] = []
    try:
        params = {
            "type": "domain_organic",
            "key": _api_key(),
            "domain": domain,
            "database": database or "us",
            "display_limit": limit,
            "display_sort": "tr_desc",          # traffic, highest first
            "export_columns": "Ph,Po,Nq,Ur",
        }
        resp = _get(settings.SEMRUSH_API_BASE + "/", params)
        for row in _parse_tabular_response(resp.text):
            out.append(OrganicKeyword(
                phrase=row.get("Keyword", row.get("Ph", "")),
                position=_safe_int(row.get("Position", row.get("Po"))) or 0,
                search_volume=_safe_int(row.get("Search Volume", row.get("Nq"))),
                url=row.get("Url", row.get("URL", row.get("Ur", ""))),
            ))
    except Exception as exc:
        logger.warning("SEMrush top-traffic pages [%s/%s]: %s", domain, database, exc)
    return out
```

- [ ] **Step 2: Add a parity stub in `services/dataforseo_service.py`** — add near its other public functions (so `seo_service` can bind it under either provider):

```python
def get_top_traffic_pages(domain: str, database: str = "us", limit: int = 10) -> list:
    """
    Parity stub for the SEO interface. The content-farm trivia check (Check 1) is
    SEMrush-specific; under the DataForSEO provider it is simply skipped (returns
    no pages), so the content-farm score relies on the cheap homepage check only.
    """
    return []
```
(Confirm `OrganicKeyword` import isn't needed since we return `[]`; if the file’s lints require a typed return, `list` is fine.)

- [ ] **Step 3: Re-export in `services/seo_service.py`** — after the existing `get_organic_keywords_for_terms = _backend.get_organic_keywords_for_terms` line, add:

```python
get_top_traffic_pages = _backend.get_top_traffic_pages
```

- [ ] **Step 4: Verify the seam binds under the default provider**

Run:
```bash
.venv/Scripts/python.exe -c "from services import seo_service; print(callable(seo_service.get_top_traffic_pages))"
```
Expected: `True`

Run the full suite to confirm nothing broke: `.venv/Scripts/python.exe -m pytest -q` → 7 new + prior tests pass.

- [ ] **Step 5: Commit**

```bash
git add services/semrush_service.py services/dataforseo_service.py services/seo_service.py
git commit -m "feat: SEMrush top-traffic-pages pull via provider seam (content farm)"
```

---

## Task 4: LLM judgments (llm_service)

**Files:**
- Modify: `services/llm_service.py` (add 3 functions; mirror `classify_outbound_links`'s `_backend.chat_json` + `_parse_json` pattern. `json` and `logger` already imported.)

- [ ] **Step 1: Add the three functions** (place after `classify_link_partners`):

```python
def classify_trivia_phrases(phrases: list[str]) -> dict:
    """
    Judge what share of a site's top ranking phrases are low-value trivia/SEO-bait
    queries ('how many seconds in a day', unit conversions, generic 'what is X').
    Returns {"trivia_share": float 0..1, "examples": [up to 5 phrases]}.
    """
    if not phrases:
        return {"trivia_share": 0.0, "examples": []}
    try:
        block = "\n".join(f"- {p}" for p in phrases[:50])
        prompt_parts = [
            "You are judging whether a website is a content farm from its top search queries.",
            "Below are phrases this site ranks for, ordered by traffic.",
            "", "## Phrases", block, "",
            "## Task",
            "A 'trivia/low-value' query is generic informational filler with no commercial or "
            "expert intent — e.g. 'how many seconds in a day', 'how many cups in a liter', unit "
            "conversions, generic 'what is X' definitions. A 'real' query has topical, commercial, "
            "or expert intent.",
            'Return ONLY JSON: {"trivia_share": <0.0-1.0>, "examples": [up to 5 trivia phrases]}',
        ]
        system = "You are a meticulous SEO content-quality analyst. You always respond with valid JSON only."
        raw = _backend.chat_json(system, "\n".join(prompt_parts), max_tokens=500)
        parsed = _parse_json(raw)
        try:
            share = float(parsed.get("trivia_share", 0.0))
        except (TypeError, ValueError):
            share = 0.0
        examples = parsed.get("examples", [])
        return {"trivia_share": max(0.0, min(1.0, share)),
                "examples": [str(e) for e in examples][:5]}
    except Exception:
        logger.exception("classify_trivia_phrases failed")
        return {"trivia_share": 0.0, "examples": []}


def classify_article_quality(articles: list[dict]) -> list[dict]:
    """
    Judge whether each sampled article is low-value content-farm filler.
    `articles`: [{url, title, snippet}]. Returns [{url, is_trivia, reason}].
    """
    if not articles:
        return []
    try:
        lines = []
        for i, a in enumerate(articles):
            lines.append(
                f"[{i}] url={a.get('url', '')}\n"
                f"    title={a.get('title', '')}\n"
                f"    snippet={(a.get('snippet', '') or '')[:400]}"
            )
        prompt_parts = [
            "You are judging whether each article below is low-value content-farm filler.",
            "Low-value = generic trivia/SEO-bait (e.g. 'How Many Seconds in a Day'), thin or "
            "templated filler with no real expertise. High-value = genuine, useful, expert content.",
            "", "## Articles", "\n".join(lines), "",
            'Return ONLY JSON with key "results": an array of '
            '{"index": <int>, "is_trivia": <bool>, "reason": "short"} for EVERY article.',
        ]
        system = "You are a meticulous SEO content-quality analyst. You always respond with valid JSON only."
        raw = _backend.chat_json(system, "\n".join(prompt_parts), max_tokens=800)
        parsed = _parse_json(raw)
        out: list[dict] = []
        for it in parsed.get("results", []):
            idx = it.get("index")
            if isinstance(idx, int) and 0 <= idx < len(articles):
                out.append({
                    "url": articles[idx].get("url", ""),
                    "is_trivia": bool(it.get("is_trivia")),
                    "reason": str(it.get("reason", "")),
                })
        return out
    except Exception:
        logger.exception("classify_article_quality failed")
        return []


def assess_content_farm(signals: dict, reasons: list[str]) -> dict:
    """
    Final content-farm verdict reasoning over the heuristic signals + observations.
    Returns {"content_farm_risk": LOW|MEDIUM|HIGH, "reasoning": str, "error": None}.
    """
    default = {"content_farm_risk": "UNKNOWN", "reasoning": "", "error": None}
    try:
        prompt_parts = [
            "You are deciding whether a website is a CONTENT FARM (mass-produced low-value "
            "trivia/SEO-bait) versus a genuine content or business site.",
            "", "## Computed signals", json.dumps(signals, indent=2, default=str),
            "", "## Observations", *(f"- {r}" for r in (reasons or ["(none)"])),
            "", "## Task",
            "The strongest tells: a high share of top-traffic pages that are trivia queries, "
            "sampled homepage articles judged trivia/filler, and a homepage packed with many "
            "internal article links. A real business or genuine expert blog is NOT a content farm.",
            'Return ONLY JSON: {"content_farm_risk": "LOW|MEDIUM|HIGH", "reasoning": "2-3 sentences"}',
        ]
        system = "You are a meticulous SEO content-quality analyst. You always respond with valid JSON only."
        raw = _backend.chat_json(system, "\n".join(prompt_parts), max_tokens=400)
        parsed = _parse_json(raw)
        risk = str(parsed.get("content_farm_risk", "UNKNOWN")).upper()
        if risk not in ("LOW", "MEDIUM", "HIGH"):
            risk = "UNKNOWN"
        return {"content_farm_risk": risk, "reasoning": str(parsed.get("reasoning", "")), "error": None}
    except Exception as exc:
        logger.exception("assess_content_farm failed")
        return {**default, "error": str(exc)}
```

- [ ] **Step 2: Verify import**

Run:
```bash
.venv/Scripts/python.exe -c "from services import llm_service as m; print(all(hasattr(m,f) for f in ['classify_trivia_phrases','classify_article_quality','assess_content_farm']))"
```
Expected: `True`. Then `.venv/Scripts/python.exe -m pytest -q` → still green.

- [ ] **Step 3: Commit**

```bash
git add services/llm_service.py
git commit -m "feat: LLM judgments for content-farm trivia/article quality + verdict"
```

---

## Task 5: Config knobs

**Files:**
- Modify: `config/settings.py`

- [ ] **Step 1: Add settings** — after the `RECIPROCAL_MAX_CHECKS` / `ENABLE_RECIPROCITY` block (added by the prior feature), add:

```python
# ── Content-farm spam score ───────────────────────────────────────────────────
ENABLE_CONTENT_FARM: bool = os.getenv("ENABLE_CONTENT_FARM", "true").strip().lower() in ("1", "true", "yes")
# Homepage articles sampled + scraped for the cheap trash check.
CONTENT_FARM_SAMPLE_ARTICLES: int = int(os.getenv("CONTENT_FARM_SAMPLE_ARTICLES", "8"))
# SEMrush rows pulled (single top market) when the check escalates. ~10 units/row.
CONTENT_FARM_TOP_PAGES: int = int(os.getenv("CONTENT_FARM_TOP_PAGES", "10"))
# Secondary thin-content trigger: an article under this many body words counts as trash.
CONTENT_FARM_THIN_WORDS: int = int(os.getenv("CONTENT_FARM_THIN_WORDS", "250"))
# Escalation triggers for the (paid) SEMrush check.
CONTENT_FARM_ESCALATE_TRASH_SHARE: float = float(os.getenv("CONTENT_FARM_ESCALATE_TRASH_SHARE", "0.4"))
CONTENT_FARM_ARTICLE_LINK_COUNT: int = int(os.getenv("CONTENT_FARM_ARTICLE_LINK_COUNT", "30"))
CONTENT_FARM_KEYWORD_FOOTPRINT: int = int(os.getenv("CONTENT_FARM_KEYWORD_FOOTPRINT", "5000"))
```

- [ ] **Step 2: Verify**

Run:
```bash
.venv/Scripts/python.exe -c "from config import settings as s; print(s.ENABLE_CONTENT_FARM, s.CONTENT_FARM_SAMPLE_ARTICLES, s.CONTENT_FARM_TOP_PAGES, s.CONTENT_FARM_THIN_WORDS, s.CONTENT_FARM_ESCALATE_TRASH_SHARE, s.CONTENT_FARM_ARTICLE_LINK_COUNT, s.CONTENT_FARM_KEYWORD_FOOTPRINT)"
```
Expected: `True 8 10 250 0.4 30 5000`

- [ ] **Step 3: Commit**

```bash
git add config/settings.py
git commit -m "feat: content-farm config knobs"
```

---

## Task 6: AuditResult field + to_dict

**Files:**
- Modify: `audit/audit_engine.py` (`AuditResult` dataclass + `to_dict`)

- [ ] **Step 1: Add the field** — in `AuditResult`, after the `business_legitimacy` field (added by the prior feature), add:

```python
    # ── Content-farm spam score ───────────────────────────────────────────────
    content_farm: dict = field(default_factory=dict)   # {score, band, content_farm_risk, reasoning, ...}
```

- [ ] **Step 2: Add to `to_dict`** — after the `"business_legitimacy": self.business_legitimacy,` entry, add:

```python
            "content_farm": self.content_farm,
            "content_farm_band": self.content_farm.get("band"),
            "content_farm_score": self.content_farm.get("score"),
```

- [ ] **Step 3: Verify**

Run:
```bash
.venv/Scripts/python.exe -c "from audit.audit_engine import AuditResult; r=AuditResult(domain='x', input_url='http://x'); d=r.to_dict(); print(d['content_farm'], d['content_farm_band'], d['content_farm_score'])"
```
Expected: `{} None None`. Then `.venv/Scripts/python.exe -m pytest -q` → green.

- [ ] **Step 4: Commit**

```bash
git add audit/audit_engine.py
git commit -m "feat: AuditResult.content_farm field"
```

---

## Task 7: Orchestration in audit_domain (+ risk nudge)

**Files:**
- Modify: `audit/audit_engine.py` (`audit_domain`)

Read `audit_domain` first. Insert the content-farm block AFTER the outbound/reciprocity/legitimacy block (added by the prior feature; it ends by setting `result.reciprocal_links`) and BEFORE the `# ── SERP results` assignment. The risk nudge goes AFTER the AI-analysis section sets `result.risk_level` (search for `result.risk_level = analysis.get(`).

- [ ] **Step 1: Insert the content-farm block** (after the reciprocity block, before `# ── SERP results`):

```python
    # ── Content-farm spam score (cheap homepage check; SEMrush only if suspicious) ─
    if settings.ENABLE_CONTENT_FARM and not result.early_failed and html:
        from services import content_farm_service as cfarm

        article_links = link_checker.extract_internal_article_links(html, full_url)
        article_link_count = len(article_links)
        sampled = article_links[: settings.CONTENT_FARM_SAMPLE_ARTICLES]

        def _fetch_article(url: str) -> Optional[dict]:
            try:
                a_html, a_err = bdata.scrape_page(url)
                if a_err or not a_html:
                    return None
                text = link_checker.extract_page_text(a_html)
                title = ""
                for ln in text.splitlines():
                    if ln.startswith("Title:"):
                        title = ln[len("Title:"):].strip()
                        break
                return {"url": url, "title": title, "snippet": text[:600],
                        "word_count": len(text.split())}
            except Exception as exc:
                logger.warning("[%s] content-farm article fetch failed %s: %s", domain, url, exc)
                return None

        fetched: list[dict] = []
        if sampled:
            with ThreadPoolExecutor(max_workers=min(settings.INNER_CONCURRENCY, len(sampled))) as pool:
                fetched = [a for a in pool.map(_fetch_article, sampled) if a]

        judged = ai_service.classify_article_quality(
            [{"url": a["url"], "title": a["title"], "snippet": a["snippet"]} for a in fetched]
        )
        _trivia_by_url = {j["url"]: j["is_trivia"] for j in judged}
        check2 = cfarm.evaluate_articles(
            [{"url": a["url"], "is_trivia": _trivia_by_url.get(a["url"], False),
              "word_count": a["word_count"]} for a in fetched],
            settings.CONTENT_FARM_THIN_WORDS,
        )

        keyword_footprint = overview.organic_keywords or 0
        escalate = cfarm.should_escalate(
            check2["trash_share"], article_link_count, keyword_footprint,
            trash_threshold=settings.CONTENT_FARM_ESCALATE_TRASH_SHARE,
            link_threshold=settings.CONTENT_FARM_ARTICLE_LINK_COUNT,
            footprint_threshold=settings.CONTENT_FARM_KEYWORD_FOOTPRINT,
        )

        trivia_share = None
        if escalate:
            _top_market = (getattr(overview, "top_databases", None) or ["us"])[0]
            logger.info("[%s] Content-farm: escalating to SEMrush (market=%s)…", domain, _top_market)
            pages = semrush.get_top_traffic_pages(domain, _top_market, settings.CONTENT_FARM_TOP_PAGES)
            if pages:
                trivia_share = ai_service.classify_trivia_phrases([p.phrase for p in pages]).get("trivia_share")

        _semrush_checked = bool(escalate and trivia_share is not None)
        cf = cfarm.compute_signals(
            trivia_share=trivia_share, trash_share=check2["trash_share"],
            judged_articles=check2["judged"], article_link_count=article_link_count,
            keyword_footprint=keyword_footprint, semrush_checked=_semrush_checked,
        )
        _verdict = ai_service.assess_content_farm(cf["signals"], cf["reasons"])
        _cf_risk = _verdict.get("content_farm_risk") or cf["band"]
        if _cf_risk == "UNKNOWN":
            _cf_risk = cf["band"]
        _floor = {"LOW": 10, "MEDIUM": 30, "HIGH": 60}.get(_cf_risk, 0)
        result.content_farm = {
            "content_farm_risk": _cf_risk,
            "score": max(cf["score"], _floor),
            "band": cf["band"],
            "reasoning": _verdict.get("reasoning", ""),
            "reasons": cf["reasons"],
            "trivia_share": trivia_share,
            "trash_share": check2["trash_share"],
            "trash_examples": check2["trash_examples"],
            "article_link_count": article_link_count,
            "semrush_checked": _semrush_checked,
            "signals": cf["signals"],
        }
        logger.info("[%s] Content-farm: %s (score %s, semrush_checked=%s)",
                    domain, _cf_risk, result.content_farm["score"], _semrush_checked)
```

- [ ] **Step 2: Add the risk nudge** — immediately AFTER the line that sets `result.risk_level = analysis.get("risk_level", ...)` in the AI-analysis section, add:

```python
    # A clear content-farm verdict shouldn't let a domain read as fully clean.
    if (result.content_farm or {}).get("content_farm_risk") == "HIGH" and \
            result.risk_level in (None, "UNKNOWN", "NO_RISK", "CLEAN", "LOW"):
        result.risk_level = "MEDIUM"
```

- [ ] **Step 3: Verify import + a no-op construction**

Run: `.venv/Scripts/python.exe -c "import audit.audit_engine; print('ok')"` → `ok`.
Run the full suite: `.venv/Scripts/python.exe -m pytest -q` → green.

- [ ] **Step 4: Commit**

```bash
git add audit/audit_engine.py
git commit -m "feat: content-farm orchestration in audit_domain with risk nudge"
```

## Escalation note for the implementer
If the anchor points differ (no reciprocity block, or `result.risk_level` set differently), STOP and report NEEDS_CONTEXT with the actual surrounding code rather than guessing — this block must not break the existing flow.

---

## Task 8: UI banner (mirror the PBN banner)

**Files:**
- Modify: `app.py`

Read `app.py` around lines 349–360 — the PBN banner (`if result.pbn and result.pbn.get("pbn_risk"):`). Add a content-farm banner immediately after it, before the `tabs = st.tabs([...])` line (~381).

- [ ] **Step 1: Add the banner** (after the PBN banner block, before tab creation):

```python
        # ── Content-farm banner ────────────────────────────────────────────
        cfarm = getattr(result, "content_farm", None) or {}
        if cfarm.get("content_farm_risk"):
            _cf_risk = cfarm.get("content_farm_risk", "UNKNOWN")
            _cf_emoji = {"LOW": "🟢", "MEDIUM": "🟠", "HIGH": "🔴"}.get(_cf_risk, "❓")
            st.markdown(
                f"**{_cf_emoji} Content-Farm Risk: {_cf_risk}**  "
                f"(score {cfarm.get('score', 0)}/100"
                + ("" if cfarm.get("semrush_checked") else ", homepage-only — SEMrush skipped") + ")"
            )
            if cfarm.get("reasoning"):
                st.caption(cfarm["reasoning"])
            for _rsn in (cfarm.get("reasons") or []):
                st.caption(f"• {_rsn}")
            if cfarm.get("trash_examples"):
                st.caption("Sample trash articles: " + ", ".join(cfarm["trash_examples"][:3]))
```

- [ ] **Step 2: Verify it parses/compiles**

Run: `.venv/Scripts/python.exe -m py_compile app.py` → no error.
Run: `.venv/Scripts/python.exe -c "import ast; ast.parse(open('app.py',encoding='utf-8').read()); print('parse ok')"` → `parse ok`.
Do NOT launch streamlit (it blocks).
Run the full suite: `.venv/Scripts/python.exe -m pytest -q` → green.

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: content-farm risk banner in UI"
```

---

## Task 9: Docs + full regression

**Files:**
- Modify: `README.md`, `.env.example`

- [ ] **Step 1: Full regression**

Run: `.venv/Scripts/python.exe -m pytest -q`. Expect all tests pass (prior + 7 new from Tasks 1–2). If anything fails, STOP and report BLOCKED.

- [ ] **Step 2: Document** — read `README.md` and `.env.example` first to match style. Add to README (near the risk-scoring section) a concise "Content-farm spam score" subsection: cheap homepage-article check (LLM judges trivia/thin), escalates to a bounded SEMrush top-traffic-pages pull (single market) ONLY when a lot of articles are trash, the homepage links to 30+ internal articles, or the keyword footprint is huge; produces a 0–100 score + LOW/MED/HIGH; SEMrush is skipped for clean sites. List the new env vars. Add to `.env.example` (with short comments):

```
# Content-farm spam score
ENABLE_CONTENT_FARM=true
CONTENT_FARM_SAMPLE_ARTICLES=8
CONTENT_FARM_TOP_PAGES=10
CONTENT_FARM_THIN_WORDS=250
CONTENT_FARM_ESCALATE_TRASH_SHARE=0.4
CONTENT_FARM_ARTICLE_LINK_COUNT=30
CONTENT_FARM_KEYWORD_FOOTPRINT=5000
```

- [ ] **Step 3: Commit**

```bash
git add README.md .env.example
git commit -m "docs: document content-farm spam score + env vars"
```

---

## Self-Review Notes (for the implementer)

- **Provider seam (Task 3):** `get_top_traffic_pages` must exist on BOTH backends or `seo_service` import fails under the other provider. The DataForSEO stub returns `[]` (Check 1 simply won't run under DataForSEO — documented, intentional).
- **`pool.map` safety (Task 7):** `_fetch_article` is wrapped in try/except and returns `None` on failure (filtered out) so one bad article never aborts the audit — same discipline the reciprocity pool required.
- **Cost guard (Task 7):** `semrush.get_top_traffic_pages` is called ONLY inside `if escalate:`. Legit sites spend 0 SEMrush units. Confirm there's no path that calls it unconditionally.
- **Scoring vs escalation thresholds:** escalation thresholds come from `settings` (via `should_escalate`); the scoring weights inside `compute_signals` are inline and tunable. These are deliberately separate.
- **Skips on early-fail:** the whole block is guarded by `not result.early_failed`, so gambling/porn hard-failed domains never run the content-farm check.

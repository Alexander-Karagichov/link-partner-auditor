# PBN / Link-Scheme Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the raw outbound-domain-count PBN signal with reciprocity + business-legitimacy analysis, add a first-step homepage gambling/porn hard-stop gate, and surface existing core-SERP / markets data in the UI.

**Architecture:** Pure, unit-testable helpers (link extraction, same-entity detection, allowlist matching, legitimacy heuristics, reciprocal link-back detection) live in the `services/` modules. The `audit_engine.audit_domain` orchestration calls them in a new order: scrape homepage → gambling/porn gate (hard stop) → parallel data wave → outbound classification → reciprocity scrapes → legitimacy → PBN scoring → AI verdict. One new AI call classifies non-allowlisted outbound domains into legit/strange/gambling_porn.

**Tech Stack:** Python 3, BeautifulSoup (lxml), Streamlit, Bright Data (scraping), SEMrush, OpenAI/Anthropic. Tests via `pytest` (added in Task 0).

**Spec:** `docs/superpowers/specs/2026-06-14-pbn-link-scheme-detection-design.md`

**Conventions in this codebase to follow:**
- `_extract_domain(href)` returns lowercase netloc (already strips a leading `www.`). Reuse it; do not reimplement.
- `_is_match(href_domain, base)` = `href_domain == base or href_domain.endswith("." + base)`. Reuse for own-entity/subdomain tests.
- Service modules are flat functions with module-level caches + a `reload_*()` to clear them (see `_load_bad_domains` / `reload_bad_domains`).
- `scrape_page(url) -> (html|None, error|None)`.

---

## File Structure

**Create:**
- `tests/` — pytest suite (new; no tests exist yet)
- `tests/test_outbound_classifier.py`
- `tests/test_legitimacy_service.py`
- `tests/test_link_checker_reciprocity.py`
- `services/outbound_classifier.py` — same-entity + allowlist bucketing of outbound domains
- `services/legitimacy_service.py` — heuristic business-legitimacy detection
- `data/legit_domains.txt` — maintainable allowlist of known-good outbound domains

**Modify:**
- `services/link_checker_service.py` — add `extract_all_external_links`, `extract_hreflang_alternates`, `links_back`
- `services/llm_service.py` — add `classify_link_partners`
- `config/settings.py` — add `RECIPROCAL_MAX_CHECKS`, `ENABLE_RECIPROCITY`, `LEGIT_DOMAINS_FILE`
- `audit/audit_engine.py` — new `AuditResult` fields + `to_dict` + restructured `audit_domain`
- `services/pbn_service.py` — `compute_signals` rework
- `app.py` — UI wiring (markets, core-SERP, new PBN fields), remove stale `rankings_error`
- `services/bright_data_service.py` — remove dead `site_search_core_keywords`
- `requirements.txt` — add `pytest`

---

## Task 0: Add pytest + tests scaffold

**Files:**
- Modify: `requirements.txt`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Add pytest to requirements**

Append to `requirements.txt`:

```
# Testing
pytest>=8.0.0
```

- [ ] **Step 2: Install**

Run: `pip install pytest`
Expected: pytest installs successfully.

- [ ] **Step 3: Create tests package**

Create `tests/__init__.py` (empty file).

Create `tests/conftest.py`:

```python
"""Make the project root importable in tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

- [ ] **Step 4: Verify pytest runs (collects 0 tests)**

Run: `pytest -q`
Expected: "no tests ran" (exit 5) or 0 collected — confirms discovery works.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt tests/__init__.py tests/conftest.py
git commit -m "test: add pytest scaffold"
```

---

## Task 1: All-links extractor + hreflang alternates (link_checker_service)

These power the gate, outbound classification, and reciprocity — all need links **including** footer/nav (unlike `extract_body_external_links`, which excludes them — reciprocal PBN links live in footers).

**Files:**
- Modify: `services/link_checker_service.py` (add functions after `extract_body_external_links`, ~line 324)
- Test: `tests/test_link_checker_reciprocity.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_link_checker_reciprocity.py`:

```python
from services import link_checker_service as lc


def test_extract_all_external_links_includes_footer():
    html = """
    <html><body>
      <main><a href="https://partner-a.com/page">A</a></main>
      <footer><a href="https://partner-b.com">B</a>
              <a href="https://example.com/internal">self</a></footer>
    </body></html>
    """
    links = lc.extract_all_external_links(html, "https://example.com")
    domains = {lc._extract_domain(h) for h in links}
    assert "partner-a.com" in domains
    assert "partner-b.com" in domains   # footer link IS included
    assert "example.com" not in domains  # internal excluded


def test_extract_hreflang_alternates():
    html = """
    <head>
      <link rel="alternate" hreflang="es" href="https://brightdata.es/">
      <link rel="alternate" hreflang="de" href="https://brightdata.de/">
      <link rel="canonical" href="https://brightdata.com/">
    </head>
    """
    alts = lc.extract_hreflang_alternates(html)
    assert alts == {"brightdata.es", "brightdata.de"}


def test_links_back_true_when_partner_links_to_us():
    partner_html = '<footer><a href="https://example.com/">friend</a></footer>'
    assert lc.links_back(partner_html, "https://partner.com", "example.com") is True


def test_links_back_false_when_no_link():
    partner_html = '<a href="https://unrelated.com/">x</a>'
    assert lc.links_back(partner_html, "https://partner.com", "example.com") is False
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/test_link_checker_reciprocity.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'extract_all_external_links'`.

- [ ] **Step 3: Implement the three functions**

In `services/link_checker_service.py`, add after `extract_body_external_links` (after line 324):

```python
def extract_all_external_links(html: str, source_url: str) -> list[str]:
    """
    Like extract_body_external_links but INCLUDES nav/header/footer links.
    Reciprocal/PBN links commonly live in footers and blogrolls, so the
    outbound-classification and reciprocity checks must see them.

    Returns a deduplicated list of external href strings.
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return []

    source_domain = _extract_domain(source_url)
    seen: set[str] = set()
    links: list[str] = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        href_domain = _extract_domain(href)
        if not href_domain:
            continue
        if source_domain and href_domain.endswith(source_domain):
            continue  # internal
        if href not in seen:
            seen.add(href)
            links.append(href)
    return links


def extract_hreflang_alternates(html: str) -> set[str]:
    """
    Return the set of domains declared as language/region alternates via
    <link rel="alternate" hreflang="..." href="..."> — these are the same
    company's other-language sites (e.g. brightdata.es for brightdata.com)
    and must be treated as own-entity, not strange outbound links.
    """
    out: set[str] = set()
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return out
    for tag in soup.find_all("link", attrs={"rel": True, "href": True}):
        rels = tag.get("rel") or []
        rels = rels if isinstance(rels, list) else [rels]
        if "alternate" not in [r.lower() for r in rels]:
            continue
        if not tag.get("hreflang"):
            continue
        d = _extract_domain(tag["href"].strip())
        if d:
            out.add(d)
    return out


def links_back(partner_html: str, partner_url: str, audited_domain: str) -> bool:
    """
    Return True if the partner page links back to *audited_domain* anywhere
    (body, nav, or footer). Used for reciprocal-link detection.
    """
    if not partner_html or not audited_domain:
        return False
    audited = audited_domain.lower().lstrip("www.")
    for href in extract_all_external_links(partner_html, partner_url):
        d = _extract_domain(href)
        if d and (d == audited or d.endswith("." + audited)):
            return True
    return False
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_link_checker_reciprocity.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add services/link_checker_service.py tests/test_link_checker_reciprocity.py
git commit -m "feat: all-links extractor, hreflang alternates, reciprocal link-back detection"
```

---

## Task 2: Legit-domain allowlist + outbound classifier

Buckets each outbound domain into own_entity / legit / candidate (candidates go to the AI in Task 5).

**Files:**
- Create: `data/legit_domains.txt`
- Create: `services/outbound_classifier.py`
- Test: `tests/test_outbound_classifier.py`

- [ ] **Step 1: Create the allowlist seed file**

Create `data/legit_domains.txt`:

```
# Known-good outbound domains. A homepage linking to these is NOT suspicious.
# Match is by registrable host or any subdomain (e.g. maps.google.com matches google.com).
# One domain per line. Lines starting with # are comments. Update as needed.

# Social
facebook.com
instagram.com
linkedin.com
youtube.com
twitter.com
x.com
tiktok.com
pinterest.com
reddit.com
threads.net
wa.me
whatsapp.com
t.me
telegram.org

# Maps / reviews / trust
google.com
goo.gl
maps.google.com
trustpilot.com
yelp.com
bbb.org

# Payments / commerce trust badges
paypal.com
stripe.com
visa.com
mastercard.com
shopify.com

# Dev / tooling / info
github.com
gitlab.com
wikipedia.org
openai.com
anthropic.com
```

- [ ] **Step 2: Write failing tests**

Create `tests/test_outbound_classifier.py`:

```python
from services import outbound_classifier as oc


def test_is_legit_matches_subdomain():
    legit = ["google.com", "facebook.com"]
    assert oc.is_legit_domain("maps.google.com", legit) is True
    assert oc.is_legit_domain("facebook.com", legit) is True
    assert oc.is_legit_domain("notgoogle.com", legit) is False


def test_is_own_entity_subdomain_and_hreflang():
    alts = {"brightdata.es", "brightdata.de"}
    assert oc.is_own_entity("docs.brightdata.com", "brightdata.com", alts) is True
    assert oc.is_own_entity("brightdata.es", "brightdata.com", alts) is True
    assert oc.is_own_entity("randomsite.com", "brightdata.com", alts) is False


def test_classify_buckets():
    html = """
    <head><link rel="alternate" hreflang="es" href="https://acme.es/"></head>
    <body>
      <a href="https://docs.acme.com/x">docs</a>
      <a href="https://acme.es/">spanish</a>
      <a href="https://facebook.com/acme">fb</a>
      <a href="https://weird-blog-network.xyz/">weird</a>
    </body>
    """
    result = oc.classify_outbound(html, "https://acme.com",
                                  legit_domains=["facebook.com"])
    assert "acme.es" in result["own_entity"]
    assert "docs.acme.com" in result["own_entity"]
    assert "facebook.com" in result["legit"]
    assert "weird-blog-network.xyz" in result["candidates"]
```

- [ ] **Step 3: Run tests, verify fail**

Run: `pytest tests/test_outbound_classifier.py -v`
Expected: FAIL — module `outbound_classifier` not found.

- [ ] **Step 4: Implement the module**

Create `services/outbound_classifier.py`:

```python
"""
Classify a page's outbound external domains into:
  - own_entity : the audited site's own subdomains + declared hreflang variants
  - legit      : domains on the maintained allowlist (data/legit_domains.txt)
  - candidates : everything left over → passed to the AI to split legit/strange

own_entity and legit links are IGNORED by PBN scoring (no points up or down).
Only the AI-confirmed 'strange' subset of candidates feeds reciprocity + scoring.
"""
from __future__ import annotations

import logging
from typing import Optional

from config import settings
from services.link_checker_service import (
    _extract_domain,
    extract_all_external_links,
    extract_hreflang_alternates,
)

logger = logging.getLogger(__name__)

_legit_cache: Optional[list[str]] = None


def _load_legit_domains() -> list[str]:
    global _legit_cache
    if _legit_cache is not None:
        return _legit_cache
    domains: list[str] = []
    try:
        path = settings.LEGIT_DOMAINS_FILE
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip().lower()
                if line and not line.startswith("#"):
                    domains.append(line)
    except Exception as exc:
        logger.warning("Could not read legit domains file: %s", exc)
    _legit_cache = domains
    return _legit_cache


def reload_legit_domains() -> None:
    global _legit_cache
    _legit_cache = None


def is_legit_domain(domain: str, legit_domains: list[str]) -> bool:
    d = (domain or "").lower().lstrip("www.")
    return any(d == g or d.endswith("." + g) for g in legit_domains)


def is_own_entity(domain: str, audited_domain: str, hreflang_alternates: set[str]) -> bool:
    d = (domain or "").lower().lstrip("www.")
    base = (audited_domain or "").lower().lstrip("www.")
    if base and (d == base or d.endswith("." + base)):
        return True
    return d in hreflang_alternates


def classify_outbound(html: str, source_url: str,
                      legit_domains: Optional[list[str]] = None) -> dict:
    """
    Return {"own_entity": [...], "legit": [...], "candidates": [...]} of
    distinct outbound domains. `legit_domains` defaults to the allowlist file.
    """
    legit = legit_domains if legit_domains is not None else _load_legit_domains()
    audited = _extract_domain(source_url)
    alternates = extract_hreflang_alternates(html)

    own: list[str] = []
    ok: list[str] = []
    candidates: list[str] = []
    seen: set[str] = set()

    for href in extract_all_external_links(html, source_url):
        d = _extract_domain(href)
        if not d or d in seen:
            continue
        seen.add(d)
        if is_own_entity(d, audited, alternates):
            own.append(d)
        elif is_legit_domain(d, legit):
            ok.append(d)
        else:
            candidates.append(d)

    return {"own_entity": own, "legit": ok, "candidates": candidates}
```

- [ ] **Step 5: Run tests, verify pass**

Run: `pytest tests/test_outbound_classifier.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add services/outbound_classifier.py data/legit_domains.txt tests/test_outbound_classifier.py
git commit -m "feat: outbound domain classifier + legit allowlist"
```

---

## Task 3: Business-legitimacy heuristics

**Files:**
- Create: `services/legitimacy_service.py`
- Test: `tests/test_legitimacy_service.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_legitimacy_service.py`:

```python
from services import legitimacy_service as ls


def test_detects_contact_signals():
    html = """
    <html><body>
      <a href="mailto:info@acme.com">email</a>
      <a href="tel:+1-202-555-0143">call</a>
      <script type="application/ld+json">
      {"@type":"LocalBusiness","name":"Acme","address":"123 Main St, Boston, MA"}
      </script>
      <footer>123 Main Street, Boston, MA 02101</footer>
    </body></html>
    """
    text = "Acme Ltd. Contact us at 123 Main Street, Boston."
    out = ls.assess(html, text)
    sig = out["signals"]
    assert sig["email"] is True
    assert sig["phone"] is True
    assert sig["schema_org_business"] is True
    assert sig["address"] is True
    assert out["is_legit"] is True
    assert out["score"] >= 2


def test_empty_page_not_legit():
    out = ls.assess("<html><body>buy now</body></html>", "buy now cheap")
    assert out["is_legit"] is False
    assert out["score"] == 0
```

- [ ] **Step 2: Run tests, verify fail**

Run: `pytest tests/test_legitimacy_service.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the module**

Create `services/legitimacy_service.py`:

```python
"""
Heuristic 'is this a legitimate business' detector.

Scans a site's homepage HTML + extracted text (and About-page text when given)
for concrete business signals: email, phone, physical address, schema.org
Organization/LocalBusiness markup, and a contact/about page link.

Returns a structured signal set + a simple score. The LLM (assess_pbn) does the
nuanced weighing; this just supplies cheap, explicit evidence. A legit business
dampens PBN risk; a total absence of these signals raises it.
"""
from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{6,}\d)")
# Loose, locale-agnostic street-address cue: a number followed by a street word.
_ADDRESS_RE = re.compile(
    r"\d{1,5}\s+\w+.{0,30}?\b("
    r"street|st\.|avenue|ave\.|road|rd\.|blvd|boulevard|lane|ln\.|drive|dr\.|"
    r"suite|ste\.|floor|fl\.|way|רחוב|улица|rue|straße|strasse"
    r")\b",
    re.IGNORECASE,
)
_SCHEMA_BUSINESS_RE = re.compile(
    r'"@type"\s*:\s*"(LocalBusiness|Organization|Corporation|Store)"', re.IGNORECASE
)
_CONTACT_HINT_RE = re.compile(r"(contact|about|impressum|אודות|צור קשר)", re.IGNORECASE)


def detect_signals(html: str, text: str) -> dict:
    html = html or ""
    text = text or ""
    blob = f"{text}\n{html}"
    return {
        "email": bool(_EMAIL_RE.search(blob)) or "mailto:" in html.lower(),
        "phone": bool(_PHONE_RE.search(text)) or "tel:" in html.lower(),
        "address": bool(_ADDRESS_RE.search(blob)),
        "schema_org_business": bool(_SCHEMA_BUSINESS_RE.search(html)),
        "contact_or_about": bool(_CONTACT_HINT_RE.search(blob)),
    }


def assess(html: str, text: str) -> dict:
    """Return {is_legit, score, signals}. score = count of distinct signals (0-5)."""
    signals = detect_signals(html, text)
    score = sum(1 for v in signals.values() if v)
    # 'is_legit' when at least two independent business signals are present, OR
    # schema.org business markup alone (an explicit, hard-to-fake declaration).
    is_legit = score >= 2 or signals["schema_org_business"]
    return {"is_legit": is_legit, "score": score, "signals": signals}
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_legitimacy_service.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add services/legitimacy_service.py tests/test_legitimacy_service.py
git commit -m "feat: heuristic business-legitimacy detector"
```

---

## Task 4: AI link-partner classifier (llm_service)

Splits non-allowlisted candidate domains into legit / strange / gambling_porn in ONE call. Serves both the gate (gambling_porn) and the strange set.

**Files:**
- Modify: `services/llm_service.py` (add after `classify_outbound_links`, ~line 316)

- [ ] **Step 1: Implement `classify_link_partners`**

Mirrors `classify_outbound_links` exactly: builds a prompt, calls
`_backend.chat_json(system, prompt, max_tokens=...)`, parses with `_parse_json`
(which returns a **dict**), and reads a single top-level key. Add after line 314:

```python
def classify_link_partners(page_url: str, domains: list[str]) -> list[dict]:
    """
    Classify each candidate outbound domain as:
      - "legit"          : a real business/utility/info site
      - "strange"        : unrelated/odd site with no clear reason to be linked
                           (link-scheme candidate → reciprocity check)
      - "gambling_porn"  : gambling or adult site (triggers immediate hard-fail)

    Returns [{"domain": str, "category": str, "reason": str}, ...].
    Returns [] on error or empty input (caller treats as 'no strange/bad links').
    """
    if not domains:
        return []
    try:
        domains_block = "\n".join(f"- {d}" for d in domains[:60])
        prompt_parts = [
            f"You are auditing the homepage outbound links of: {page_url}",
            "Classify EACH linked domain below to spot link schemes and disallowed content.",
            "",
            "## Linked domains",
            domains_block,
            "",
            "## Task",
            "For EACH domain choose exactly one `category`:",
            "  - `gambling_porn`: gambling, casino, betting, or adult/porn site",
            "  - `legit`: a recognizable real business, tool, or info resource",
            "  - `strange`: unrelated/odd site with no obvious reason to be linked "
            "(possible link-exchange partner)",
            "",
            "Return a JSON object with a single key `results` containing an array of "
            "objects, each with `domain`, `category`, and a one-sentence `reason`.",
            "Return ONLY the JSON object, no markdown fences.",
        ]
        system = (
            "You are a meticulous SEO link-network analyst. "
            "You always respond with valid JSON only."
        )
        raw = _backend.chat_json(system, "\n".join(prompt_parts), max_tokens=800)
        parsed = _parse_json(raw)
        out: list[dict] = []
        for it in parsed.get("results", []):
            if isinstance(it, dict) and it.get("domain"):
                out.append({
                    "domain": str(it["domain"]).lower().lstrip("www."),
                    "category": str(it.get("category", "strange")).lower(),
                    "reason": str(it.get("reason", "")),
                })
        return out
    except Exception:
        logger.exception("classify_link_partners failed for %s", page_url)
        return []
```

- [ ] **Step 2: Smoke-check import**

Run: `python -c "from services import llm_service; print(hasattr(llm_service,'classify_link_partners'))"`
Expected: `True`

- [ ] **Step 3: Commit**

```bash
git add services/llm_service.py
git commit -m "feat: AI classifier splitting outbound domains into legit/strange/gambling_porn"
```

---

## Task 5: Config knobs

**Files:**
- Modify: `config/settings.py` (Bright Data section ~line 72 for caps; File Paths section ~line 83 for the file)

- [ ] **Step 1: Add reciprocity + legitimacy settings**

After `SERP_MAX_TERMS` (~line 72) add:

```python
# Reciprocal-link (PBN) check: max strange outbound domains whose homepage we
# fetch to see if they link back. Each = 1 Bright Data scrape. 0 disables.
RECIPROCAL_MAX_CHECKS: int = int(os.getenv("RECIPROCAL_MAX_CHECKS", "10"))
ENABLE_RECIPROCITY: bool = os.getenv("ENABLE_RECIPROCITY", "true").strip().lower() in ("1", "true", "yes")
```

In the File Paths section (after `LINKBUILDING_TARGETS_FILE`, ~line 85) add:

```python
LEGIT_DOMAINS_FILE: Path = DATA_DIR / "legit_domains.txt"
```

- [ ] **Step 2: Verify**

Run: `python -c "from config import settings; print(settings.RECIPROCAL_MAX_CHECKS, settings.ENABLE_RECIPROCITY, settings.LEGIT_DOMAINS_FILE.name)"`
Expected: `10 True legit_domains.txt`

- [ ] **Step 3: Commit**

```bash
git add config/settings.py
git commit -m "feat: add reciprocity + legit-domains config"
```

---

## Task 6: AuditResult fields + to_dict

**Files:**
- Modify: `audit/audit_engine.py` (`AuditResult` dataclass ~lines 155-188; `to_dict` ~lines 206-245)

- [ ] **Step 1: Add fields**

In the `AuditResult` dataclass, after the SERP fields block (after `serp_core_error`, ~line 184) add:

```python
    # ── Early hard-fail (homepage gambling/porn gate) ─────────────────────────
    early_failed: bool = False
    early_fail_reason: Optional[str] = None

    # ── Outbound link analysis (PBN / link scheme) ────────────────────────────
    outbound_classification: dict = field(default_factory=dict)   # {own_entity, legit, strange}
    reciprocal_links: list[dict] = field(default_factory=list)    # [{partner, links_back, partner_legit}]
    business_legitimacy: dict = field(default_factory=dict)       # {is_legit, score, signals}
```

- [ ] **Step 2: Add to_dict entries**

In `to_dict`, after the serp_core entries (~line 240) add:

```python
            "early_failed": self.early_failed,
            "early_fail_reason": self.early_fail_reason,
            "outbound_classification": self.outbound_classification,
            "reciprocal_links": self.reciprocal_links,
            "reciprocal_strange_link_count": sum(
                1 for r in self.reciprocal_links if r.get("links_back")
            ),
            "business_legitimacy": self.business_legitimacy,
```

- [ ] **Step 3: Verify import + construction**

Run: `python -c "from audit.audit_engine import AuditResult; r=AuditResult(domain='x', input_url='http://x'); print(r.to_dict()['early_failed'], r.to_dict()['reciprocal_strange_link_count'])"`
Expected: `False 0`

- [ ] **Step 4: Commit**

```bash
git add audit/audit_engine.py
git commit -m "feat: AuditResult fields for gate, outbound, reciprocity, legitimacy"
```

---

## Task 7: Homepage gambling/porn gate as the first step (audit_domain)

Restructure the top of `audit_domain` so the homepage is scraped and gated **before** any other network call.

**Files:**
- Modify: `audit/audit_engine.py` (`audit_domain`, current Wave 1 ~lines 302-326)
- Also: add `reload_legit_domains` to `reload_keywords` (~line 96)

- [ ] **Step 1: Add a helper for the gate (module level, near other helpers ~line 100)**

```python
def _homepage_gambling_gate(domain: str, html: str) -> tuple[bool, list[dict], Optional[str]]:
    """
    Decide if the homepage links DIRECTLY to gambling/porn — an instant fail.
    Returns (failed, offending_links, reason).

    Two detectors: known_bad_sites.txt match (free) then, only if clean, the AI
    classifier on non-allowlisted outbound domains.
    """
    from services import outbound_classifier as oc

    offending: list[dict] = []

    # 1. Known-bad list match (free).
    link_result = link_checker.check_links(html, f"https://{domain}")
    for m in link_result.bad_link_matches:
        offending.append({
            "found_href": m.found_href,
            "matched_bad_domain": m.matched_bad_domain,
            "link_text": m.link_text,
        })
    if offending:
        return True, offending, "Homepage links to a known gambling/adult site."

    # 2. AI classify non-allowlisted candidates.
    buckets = oc.classify_outbound(html, f"https://{domain}")
    candidates = buckets.get("candidates", [])
    if candidates:
        verdicts = ai_service.classify_link_partners(f"https://{domain}", candidates)
        for v in verdicts:
            if v.get("category") == "gambling_porn":
                offending.append({
                    "found_href": v["domain"],
                    "matched_bad_domain": f"[AI: {v.get('reason', 'gambling/adult')}]",
                    "link_text": "",
                })
        if offending:
            return True, offending, "Homepage links to an AI-detected gambling/adult site."

    return False, [], None
```

- [ ] **Step 2: Restructure the start of `audit_domain`**

Replace the current Wave-1 block (lines 308-326, from the `# ── Wave 1` comment through `domain_age_info = f_age.result()`) with:

```python
    # ── GATE (first, serial): scrape homepage, hard-fail on gambling/porn links ─
    logger.info("[%s] Gate: scraping homepage and checking for gambling/porn links…", domain)
    html, scrape_err = bdata.scrape_page(full_url)
    result.scrape_error = scrape_err
    if html:
        failed, offending, reason = _homepage_gambling_gate(domain, html)
        if failed:
            result.homepage_scraped = True
            result.bad_links_found = offending
            result.early_failed = True
            result.early_fail_reason = reason
            result.risk_level = "BAD"
            result.ai_analysis = {"summary": reason, "risk_level": "BAD"}
            logger.warning("[%s] HARD FAIL at homepage gate: %s", domain, reason)
            return result

    # ── Wave 1: fire the remaining independent network calls concurrently ──────
    logger.info("[%s] Gate passed. Running parallel data collection…", domain)
    with ThreadPoolExecutor(max_workers=8) as pool:
        f_overview = pool.submit(semrush.get_domain_overview, domain)
        f_backlinks = pool.submit(semrush.get_backlinks_overview, domain)
        f_serp = pool.submit(bdata.site_search_porn_gambling, domain)
        f_serp_core = pool.submit(bdata.site_search_core, domain)
        f_net = pool.submit(pbn_service.network_footprint, domain)
        f_age = pool.submit(pbn_service.domain_age, domain)

        overview = f_overview.result()
        backlinks = f_backlinks.result()
        serp_results, serp_err = f_serp.result()
        serp_core_results, serp_core_err = f_serp_core.result()
        network = f_net.result()
        domain_age_info = f_age.result()
```

> **Note:** `html`/`scrape_err` are now set BEFORE Wave 1, and `f_home` is removed
> from the pool. Later in the function the line `result.scrape_error = scrape_err`
> (~line 379) is now redundant — leave the homepage-processing block (`if html:`)
> as-is; it already uses the `html` variable. Remove the duplicate
> `result.scrape_error = scrape_err` assignment at ~line 379.

- [ ] **Step 3: Wire reload**

In `reload_keywords` (~line 96), add after `bdata.reload_serp_terms()`:

```python
    from services import outbound_classifier as oc
    oc.reload_legit_domains()
```

- [ ] **Step 4: Smoke test (no network) — gate helper with a known-bad link**

Run:
```bash
python -c "
from audit import audit_engine as ae
from services import link_checker_service as lc
# monkeypatch bad domains
lc._bad_domains_cache = ['royalcasino.dk']
html = '<a href=\"https://royalcasino.dk/\">play</a>'
print(ae._homepage_gambling_gate('example.com', html))
"
```
Expected: `(True, [...], 'Homepage links to a known gambling/adult site.')`
(If the cache attribute name differs, read `_load_bad_domains` for the real global name.)

- [ ] **Step 5: Commit**

```bash
git add audit/audit_engine.py
git commit -m "feat: homepage gambling/porn gate as first step with hard-stop"
```

---

## Task 8: Outbound classification + reciprocity + legitimacy in audit_domain

Runs after Wave 2 (SEMrush) and the homepage processing, before PBN scoring.

**Files:**
- Modify: `audit/audit_engine.py` (insert after the homepage `if html:` block, before "SERP results", ~line 411)

- [ ] **Step 1: Insert the new analysis block**

After the About-page block ends (~line 410, before `# ── SERP results`), insert:

```python
    # ── Outbound classification → reciprocity → legitimacy (PBN link scheme) ──
    from services import outbound_classifier as oc
    from services import legitimacy_service as legit

    # Business legitimacy of the audited site (homepage + about text).
    result.business_legitimacy = legit.assess(
        html or "", (result.homepage_text or "") + "\n" + (result.about_page_text or "")
    )

    _strange: list[str] = []
    if html:
        buckets = oc.classify_outbound(html, full_url)
        # AI splits candidates into legit/strange (gambling already gated out).
        verdicts = ai_service.classify_link_partners(full_url, buckets.get("candidates", []))
        _strange = [v["domain"] for v in verdicts if v.get("category") == "strange"]
        result.outbound_classification = {
            "own_entity": buckets.get("own_entity", []),
            "legit": buckets.get("legit", []) + [v["domain"] for v in verdicts if v.get("category") == "legit"],
            "strange": _strange,
        }

    # Reciprocity: do the strange partners link back to us?
    if _strange and settings.ENABLE_RECIPROCITY and settings.RECIPROCAL_MAX_CHECKS > 0:
        targets = _strange[: settings.RECIPROCAL_MAX_CHECKS]
        logger.info("[%s] Reciprocity check on %d strange domain(s)…", domain, len(targets))

        def _check_partner(partner: str) -> dict:
            entry = {"partner": partner, "links_back": False, "partner_legit": None}
            p_html, p_err = bdata.scrape_page(f"https://{partner}")
            if p_err or not p_html:
                return entry
            entry["links_back"] = link_checker.links_back(p_html, f"https://{partner}", domain)
            if entry["links_back"]:
                # Only deep-check legitimacy of partners that actually link back.
                p_text = link_checker.extract_page_text(p_html)
                about = link_checker.find_about_url(p_html, f"https://{partner}")
                if about:
                    a_html, a_err = bdata.scrape_page(about)
                    if a_html and not a_err:
                        p_text += "\n" + link_checker.extract_page_text(a_html)
                entry["partner_legit"] = legit.assess(p_html, p_text)["is_legit"]
            return entry

        with ThreadPoolExecutor(max_workers=min(settings.INNER_CONCURRENCY, len(targets))) as pool:
            result.reciprocal_links = list(pool.map(_check_partner, targets))
```

- [ ] **Step 2: Verify import compiles**

Run: `python -c "import audit.audit_engine"`
Expected: no error.

- [ ] **Step 3: Commit**

```bash
git add audit/audit_engine.py
git commit -m "feat: outbound classification, reciprocity scrapes, legitimacy in audit pipeline"
```

---

## Task 9: PBN scoring rework (compute_signals)

**Files:**
- Modify: `services/pbn_service.py` (`compute_signals` signature ~line 113; signal #4 ~lines 202-207)
- Modify: `audit/audit_engine.py` (the `compute_signals(...)` call ~line 530)

- [ ] **Step 1: Update `compute_signals` signature + signals**

Add two parameters to `compute_signals` (after `total_ranked_keywords`):

```python
    reciprocal_links: Optional[list[dict]] = None,
    business_legitimacy: Optional[dict] = None,
```

Add to the `signals` dict (after `"registrar"`):

```python
        "reciprocal_strange_link_count": sum(1 for r in (reciprocal_links or []) if r.get("links_back")),
        "business_is_legit": (business_legitimacy or {}).get("is_legit"),
        "business_legitimacy_score": (business_legitimacy or {}).get("score"),
```

- [ ] **Step 2: Replace signal #4 (raw outbound count)**

Replace the block at lines 202-207 (`# 4. Link-network outbound pattern.` ... `score += 8`) with:

```python
    # 4. Reciprocal strange links — the core link-scheme tell. A strange,
    #    unrelated site that links BACK to this domain is a deliberate exchange.
    recip = [r for r in (reciprocal_links or []) if r.get("links_back")]
    if recip:
        # Reciprocated partners that are themselves NOT real businesses weigh more.
        non_legit = [r for r in recip if r.get("partner_legit") is False]
        score += 25 + min(10 * len(non_legit), 20)
        reasons.append(
            f"{len(recip)} strange site(s) link back to this domain"
            + (f", {len(non_legit)} of them not real businesses" if non_legit else "")
            + " — reciprocal link-scheme pattern."
        )

    # 4b. Business legitimacy of the audited site (dampener / riser).
    if business_legitimacy is not None:
        if business_legitimacy.get("is_legit"):
            score = max(0, score - 10)
            reasons.append("Audited site shows real business signals (contacts/address/schema).")
        elif business_legitimacy.get("score", 0) == 0:
            score += 12
            reasons.append("No business-legitimacy signals (no contacts, address, or org markup).")
```

- [ ] **Step 3: Update the call in audit_engine**

In `audit_domain` (~line 530), add to the `pbn_service.compute_signals(...)` call args:

```python
        reciprocal_links=result.reciprocal_links,
        business_legitimacy=result.business_legitimacy,
```

- [ ] **Step 4: Quick unit check**

Run:
```bash
python -c "
from services import pbn_service as p
out = p.compute_signals(referring_domains=10,total_backlinks=10,organic_traffic=5000,
  authority_score=20,pg_keyword_hit_count=0,homepage_text='we sell shoes',
  distinct_external_domains=3,network={},age={},
  reciprocal_links=[{'links_back':True,'partner_legit':False}],
  business_legitimacy={'is_legit':False,'score':0})
print(out['pbn_score']); print(out['reasons'])
"
```
Expected: a score reflecting the reciprocal hit (+25 +10) and no-legit (+12), with matching reasons.

- [ ] **Step 5: Commit**

```bash
git add services/pbn_service.py audit/audit_engine.py
git commit -m "feat: PBN scoring on reciprocity + legitimacy, drop raw outbound count"
```

---

## Task 10: assess_pbn prompt — feed new evidence

**Files:**
- Modify: `services/llm_service.py` (`assess_pbn` ~lines 317-366; it already serializes `signals` as JSON, so the new signal keys flow in automatically — add explicit guidance)

- [ ] **Step 1: Add guidance lines**

In the `## Task` guidance inside `assess_pbn` (after the "strongest PBN tells" sentence ~line 350), add:

```python
            "A strange, unrelated site that links BACK to the audited domain "
            "(`reciprocal_strange_link_count` > 0) is a strong deliberate-link-exchange "
            "signal — especially if those partners are not real businesses. Conversely, "
            "clear business-legitimacy signals (`business_is_legit` true) on the audited "
            "site weigh AGAINST a PBN verdict.",
```

- [ ] **Step 2: Verify import**

Run: `python -c "from services import llm_service; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add services/llm_service.py
git commit -m "feat: assess_pbn reasons over reciprocity + legitimacy signals"
```

---

## Task 11: UI wiring — gate, markets, core-SERP, PBN evidence

**Files:**
- Modify: `app.py` (Overview tab ~line 380; SERP tab ~lines 511-527; Rankings tab stale `rankings_error` ~line 419; results header for early-fail)

- [ ] **Step 1: Early-fail banner**

At the top of the per-result render (just inside the block that builds the tabs, before `tabs = ...`), add:

```python
        if getattr(result, "early_failed", False):
            st.error(f"🛑 HARD FAIL — {result.early_fail_reason}")
            if result.bad_links_found:
                st.dataframe(pd.DataFrame(result.bad_links_found), use_container_width=True, hide_index=True)
```

> **Note:** locate the exact spot by reading `app.py` around where each `result`'s
> tabs are created; place this banner immediately before that. The rest of the tabs
> still render (fields are empty for early-failed audits, which is fine).

- [ ] **Step 2: Markets in Overview tab**

In the Overview tab, after the metrics columns (after line 392, the backlinks_error caption), add:

```python
            if getattr(result, "top_countries", None):
                st.caption("Markets checked: " + ", ".join(result.top_countries))
                if result.traffic_by_country:
                    _tbc = sorted(result.traffic_by_country.items(), key=lambda kv: kv[1], reverse=True)[:8]
                    st.dataframe(
                        pd.DataFrame(_tbc, columns=["Country DB", "Organic Traffic"]),
                        use_container_width=True, hide_index=True,
                    )
```

- [ ] **Step 3: Core-SERP block in SERP tab**

In the SERP tab, after the porn/gambling block (after line 527), add:

```python
            st.divider()
            st.markdown("**Google `site:` search for core-business content**")
            if getattr(result, "serp_core_error", None):
                st.caption(f"⚠️ Core SERP error: {result.serp_core_error}")
            if getattr(result, "serp_core_results", None):
                st.success(f"✅ Google indexes {len(result.serp_core_results)} core-business page(s).")
                st.dataframe(pd.DataFrame(result.serp_core_results), use_container_width=True, hide_index=True)
            else:
                st.info("No core-business pages surfaced via Google site: search.")
```

- [ ] **Step 4: Reciprocity + legitimacy in the Links tab**

At the end of the Links/PBN tab (after the Competitor Links block ~line 509), add:

```python
            st.subheader("Reciprocal Links & Legitimacy")
            bl = getattr(result, "business_legitimacy", {}) or {}
            if bl:
                st.caption(
                    f"Business legitimacy: {'✅ legit' if bl.get('is_legit') else '⚠️ weak'} "
                    f"(score {bl.get('score', 0)}) — signals: "
                    + ", ".join(k for k, v in (bl.get('signals') or {}).items() if v) or "none"
                )
            recip = getattr(result, "reciprocal_links", []) or []
            back = [r for r in recip if r.get("links_back")]
            if back:
                st.error(f"🔁 {len(back)} strange site(s) link back — reciprocal link-scheme pattern.")
                st.dataframe(pd.DataFrame(recip), use_container_width=True, hide_index=True)
            elif recip:
                st.success(f"Checked {len(recip)} strange outbound site(s); none link back.")
```

- [ ] **Step 5: Remove stale `rankings_error` reference**

At ~line 418-419, delete:

```python
            if result.rankings_error:
                st.caption(f"⚠️ Rankings error: {result.rankings_error}")
```

(The engine no longer sets `rankings_error`.)

- [ ] **Step 6: Manual smoke — launch the app**

Run: `streamlit run app.py` (or the project's run skill). Confirm the app starts with no import/attribute errors and the Overview/SERP/Links tabs render. Stop it after the check.

- [ ] **Step 7: Commit**

```bash
git add app.py
git commit -m "feat: UI for hard-fail, markets, core-SERP, reciprocity + legitimacy"
```

---

## Task 12: Remove dead code

**Files:**
- Modify: `services/bright_data_service.py` (`site_search_core_keywords` ~line 318)

- [ ] **Step 1: Confirm it is unused**

Run: `grep -rn "site_search_core_keywords" --include=*.py .`
Expected: only its definition in `bright_data_service.py` (no callers).

- [ ] **Step 2: Delete the function**

Remove the entire `def site_search_core_keywords(...)` definition.

- [ ] **Step 3: Verify import still works**

Run: `python -c "import services.bright_data_service; import app" 2>&1 | head -5`
Expected: no error (Streamlit import warnings about bare mode are OK).

- [ ] **Step 4: Commit**

```bash
git add services/bright_data_service.py
git commit -m "chore: remove dead site_search_core_keywords"
```

---

## Task 13: Full regression + docs

**Files:**
- Modify: `README.md` / `USAGE.md` (document the new gate, reciprocity, legitimacy, and `data/legit_domains.txt` + new env vars)

- [ ] **Step 1: Run the full test suite**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 2: Document the new behavior**

Add to `README.md` (near the existing keyword/data-file table): the homepage gambling/porn hard-fail gate, the reciprocity check, the legitimacy check, `data/legit_domains.txt`, and env vars `RECIPROCAL_MAX_CHECKS`, `ENABLE_RECIPROCITY`. Add `RECIPROCAL_MAX_CHECKS` / `ENABLE_RECIPROCITY` to `.env.example`.

- [ ] **Step 3: Commit**

```bash
git add README.md USAGE.md .env.example
git commit -m "docs: document gate, reciprocity, legitimacy, legit_domains.txt"
```

---

## Self-Review Notes (for the implementer)

- **AI call (Task 4):** uses the codebase's `_backend.chat_json(system, prompt, max_tokens=...)` + `_parse_json` (returns a dict) — same path as `classify_outbound_links`. No new client path.
- **Gate ordering:** Task 7 makes the homepage scrape serial and first; everything else stays parallel. Bad sites short-circuit before SEMrush/SERP/deep-crawl/AI-verdict.
- **What feeds scoring:** only reciprocal strange links + audited-site legitimacy. own_entity/legit/strange counts are surfaced in the UI but do not score (per spec).
- **`INNER_CONCURRENCY`** is an existing setting used elsewhere in the engine — reuse it for the reciprocity pool (don't invent a new concurrency knob).

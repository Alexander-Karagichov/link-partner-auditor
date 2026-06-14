"""
Provider-agnostic LLM analysis service.

This is the single seam the audit engine talks to (`from services import
llm_service as ai_service`). It owns the prompts, JSON parsing, and the three
public functions — `analyze_audit`, `recommend_link_building`, and
`classify_outbound_links` — and delegates the actual API call to a backend
selected by `settings.LLM_PROVIDER`:

    LLM_PROVIDER=openai      -> services.openai_service     (default; gpt-5.2)
    LLM_PROVIDER=anthropic   -> services.anthropic_service  (Claude)

Each backend exposes one function: `chat_json(system_prompt, user_prompt,
max_tokens) -> str`, returning the model's raw text (expected to be JSON).
Adding a third provider is just another module with that one function.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

# Select the backend once, at import time. Switching providers requires a
# restart — same as any other .env change (settings are read at startup).
if settings.LLM_PROVIDER == "anthropic":
    from services import anthropic_service as _backend
else:
    from services import openai_service as _backend


# ── JSON helpers ────────────────────────────────────────────────────────────────

def _strip_fences(raw: str) -> str:
    """Remove ```json ... ``` fences some models add despite instructions."""
    s = raw.strip()
    if s.startswith("```"):
        s = s[3:]
        if s[:4].lower() == "json":
            s = s[4:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def _parse_json(raw: str) -> dict:
    """Parse a model response into a dict, tolerating fences/preamble."""
    s = _strip_fences(raw)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # Last resort: extract the outermost {...} object.
        start, end = s.find("{"), s.rfind("}")
        if 0 <= start < end:
            return json.loads(s[start : end + 1])
        raise


# ── Prompt builder (analysis) ────────────────────────────────────────────────────

def _build_analyze_prompt(audit_data: dict, homepage_text: str = "", about_page_text: str = "") -> str:
    """
    Build the user message for the audit analysis.

    audit_data is the dict produced by audit_engine.py; it contains all
    SEMrush metrics, link findings, SERP results, and keyword matches.
    homepage_text / about_page_text are the scraped page texts (passed
    separately, not stored in the exported audit data) — these are the primary
    evidence for what the site actually is.
    """
    domain = audit_data.get("domain", "unknown")

    site_content = "\n\n".join(
        s for s in (
            f"[Homepage]\n{homepage_text}" if homepage_text else "",
            f"[About page]\n{about_page_text}" if about_page_text else "",
        ) if s
    )
    site_section = (
        ["## Site Content (what the site actually says about itself — scraped, not in export)",
         site_content, ""]
        if site_content
        else []
    )

    sections = [
        f"You are a senior SEO and brand-safety analyst at Bright Data.",
        f"Analyse the following automated audit result for the domain **{domain}** "
        f"and provide a structured JSON response.",
        "",
        "## Audit Data",
        json.dumps(audit_data, indent=2, default=str),
        "",
        *site_section,
        "## Task",
        "Return a JSON object with EXACTLY these keys:",
        "  - `risk_level`: one of CLEAN / LOW / MEDIUM / HIGH / CRITICAL",
        "  - `website_niche`: 3-6 word description of the site's primary topic/industry",
        "  - `summary`: 2-4 sentence plain-language summary of the domain's profile and primary purpose",
        "  - `key_findings`: list of up to 6 short bullet-point strings (most important issues)",
        "  - `recommendation`: 1-3 sentence recommendation for the Bright Data team",
        "  - `competitor_risk`: boolean – does this domain appear to directly compete with Bright Data?",
        "  - `brand_safe`: boolean – is this domain safe to associate with Bright Data?",
        "",
        "Risk level guide:",
        "  CLEAN    – no issues found, domain is reputable",
        "  LOW      – minor concerns, low priority",
        "  MEDIUM   – notable issues, needs review",
        "  HIGH     – significant red flags (gambling/adult links or rankings)",
        "  CRITICAL – domain actively hosts or promotes adult/gambling content",
        "",
        "IMPORTANT CONTEXT for risk assessment:",
        "  - If porn/gambling keyword hits come from DIRECTORY LISTINGS of businesses",
        "    (e.g. a taxi company named 'Casino Cab Co'), treat these as incidental",
        "    business-name matches, NOT as gambling content. Weight them very low.",
        "  - If SERP results for gambling terms point to pages whose URLs show no",
        "    relation to gambling (e.g. /recipes/, /write-for-us/), these are Google",
        "    false positives from the site: search. Do NOT treat as gambling signals.",
        "  - Only flag HIGH or CRITICAL if pages ACTUALLY link to or host gambling/adult content.",
        "",
        "NICHE DETECTION (important):",
        "  - Base `website_niche` and `summary` PRIMARILY on the Site Content above —",
        "    what the homepage/about page actually describe the business as.",
        "  - Ranking keywords and ranked URLs can be legacy, expired-domain, or parasite",
        "    content and must NOT override the site's own description of itself. E.g. a web",
        "    design company that also has old /taxi-directory/ pages indexed is a WEB DESIGN",
        "    company, not a taxi directory.",
        "  - If Site Content is missing, say the niche is uncertain rather than guessing from keywords.",
        "",
        "Return ONLY the JSON object, no markdown fences, no extra text.",
    ]
    return "\n".join(sections)


# ── Public API ─────────────────────────────────────────────────────────────────

def analyze_audit(audit_data: dict, about_page_text: str = "", homepage_text: str = "") -> dict:
    """
    Send audit_data to the configured LLM and return a structured analysis dict.

    On failure the returned dict includes an 'error' key and safe defaults.
    """
    default_response = {
        "risk_level": "UNKNOWN",
        "website_niche": "",
        "summary": "AI analysis could not be completed.",
        "key_findings": [],
        "recommendation": "Please review manually.",
        "competitor_risk": False,
        "brand_safe": None,
        "error": None,
    }

    try:
        system = (
            "You are a senior SEO and brand-safety analyst. "
            "You always respond with valid JSON only."
        )
        user = _build_analyze_prompt(audit_data, homepage_text=homepage_text, about_page_text=about_page_text)

        raw = _backend.chat_json(system, user, max_tokens=800)
        parsed = _parse_json(raw)

        # Merge with defaults so callers always get expected keys.
        return {**default_response, **parsed, "error": None}

    except json.JSONDecodeError as exc:
        msg = f"Failed to parse LLM JSON response: {exc}"
        logger.warning(msg)
        return {**default_response, "error": msg}
    except ValueError as exc:
        return {**default_response, "error": str(exc)}
    except Exception as exc:
        logger.exception("Unexpected error in LLM analysis")
        return {**default_response, "error": str(exc)}


def recommend_link_building(
    audit_data: dict,
    linkbuilding_targets: list[dict],
    target_domain: str,
    about_page_text: str = "",
    homepage_text: str = "",
) -> dict:
    """
    Given the full audit result plus a list of {keyword, url} link-building
    targets, ask the LLM to recommend the best anchor keyword + URL pair and a
    relevant guest-post topic for this site.
    """
    default_response = {
        "best_keyword": None,
        "target_url": None,
        "guest_post_topic": None,
        "reasoning": None,
        "error": None,
    }

    try:
        domain = audit_data.get("domain", "unknown")

        targets_block = "\n".join(
            f"- {t['keyword']} → {t['url']}" for t in linkbuilding_targets
        )

        prompt_parts = [
            f"You are an SEO link-building strategist at {target_domain}.",
            f"You are evaluating the website **{domain}** as a potential link partner.",
            "",
            "## Audit summary",
            f"- Authority Score: {audit_data.get('authority_score')}",
            f"- Organic Traffic/mo: {audit_data.get('organic_traffic')}",
            f"- Risk Level: {audit_data.get('risk_level')}",
            f"- Brand Safe: {audit_data.get('ai_brand_safe')}",
            f"- Core topic/summary: {audit_data.get('ai_analysis_summary', '')}",
            *(["", "## Site Content (what the site actually does — base the niche fit on THIS, "
               "not on ranking keywords which may be legacy/expired-domain content)",
               "\n\n".join(s for s in (
                   f"[Homepage]\n{homepage_text}" if homepage_text else "",
                   f"[About page]\n{about_page_text}" if about_page_text else "",
               ) if s)]
              if (homepage_text or about_page_text) else []),
            "",
            "## Available link-building targets (keyword → landing page URL)",
            targets_block,
            "",
            "## Task",
            f"Choose the SINGLE best keyword+URL pair from the list above that would make the most natural, topically relevant anchor text linking from {domain} back to the specified URL.",
            "Consider both (a) how well the keyword matches the site's audience and niche, and (b) how relevant the target landing page content is to that audience.",
            "Also suggest a realistic, specific guest post title the webmaster of this site could write that would naturally include that link.",
            "",
            "Return a JSON object with EXACTLY these keys:",
            "  - `best_keyword`: the chosen keyword string (must be copied exactly from the list)",
            "  - `target_url`: the corresponding URL from the list (copy it exactly, do not modify)",
            "  - `guest_post_topic`: a specific, compelling guest post title",
            "  - `reasoning`: 2-3 sentences explaining why this keyword+page fits this site's niche and audience",
            "",
            "Return ONLY the JSON object, no markdown fences, no extra text.",
        ]

        system = (
            "You are an expert SEO link-building strategist. "
            "You always respond with valid JSON only."
        )
        raw = _backend.chat_json(system, "\n".join(prompt_parts), max_tokens=400)
        parsed = _parse_json(raw)
        return {**default_response, **parsed, "error": None}

    except json.JSONDecodeError as exc:
        return {**default_response, "error": f"Failed to parse JSON: {exc}"}
    except ValueError as exc:
        return {**default_response, "error": str(exc)}
    except Exception as exc:
        logger.exception("Unexpected error in link building recommendation")
        return {**default_response, "error": str(exc)}


def classify_outbound_links(page_url: str, external_links: list[str]) -> list[dict]:
    """
    Ask the LLM to classify a list of external links from a page body as
    gambling/adult or safe.

    Returns a list of dicts {found_href, category, reason} for every link that
    is classified as gambling or adult content. Only flagged links are returned.
    """
    if not external_links:
        return []

    default_response: list[dict] = []

    try:
        links_block = "\n".join(f"- {href}" for href in external_links[:60])

        prompt_parts = [
            f"You are a brand-safety analyst reviewing outbound links found in the main body of this page: {page_url}",
            "",
            "Below is a list of external URLs linked from the body of that page (navigation and footer links have already been excluded).",
            "",
            "## Outbound links",
            links_block,
            "",
            "## Task",
            "For EACH link, determine whether the destination domain is a gambling, social-casino, sports-betting, adult/porn, or escort website.",
            "Only include links in your response that ARE gambling/adult (skip safe links).",
            "",
            "Return a JSON object with a single key `flagged_links` containing an array of objects, each with:",
            "  - `found_href`: the exact URL from the list",
            "  - `category`: one of: gambling, social_casino, sports_betting, adult, escort, other_harmful",
            "  - `reason`: one sentence explaining why it is flagged",
            "",
            "If NO links are gambling/adult, return: {\"flagged_links\": []}",
            "Return ONLY the JSON object, no markdown fences, no extra text.",
        ]

        system = (
            "You are a brand-safety content classifier. "
            "You always respond with valid JSON only."
        )
        raw = _backend.chat_json(system, "\n".join(prompt_parts), max_tokens=800)
        parsed = _parse_json(raw)
        return parsed.get("flagged_links", [])

    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse LLM JSON for outbound link classification: %s", exc)
        return default_response
    except ValueError as exc:
        logger.warning("ValueError classifying outbound links: %s", exc)
        return default_response
    except Exception as exc:
        logger.exception("Unexpected error classifying outbound links")
        return default_response


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
                    "domain": str(it["domain"]).lower().removeprefix("www."),
                    "category": str(it.get("category", "strange")).lower(),
                    "reason": str(it.get("reason", "")),
                })
        return out
    except Exception:
        logger.exception("classify_link_partners failed for %s", page_url)
        return []


def assess_pbn(signals: dict, heuristic_reasons: list[str], homepage_text: str = "",
               ranking_sample: Optional[list[str]] = None) -> dict:
    """
    Ask the LLM for a final PBN / link-network verdict, reasoning over the
    structured signals computed by pbn_service plus the actual homepage text.

    Returns {pbn_risk: LOW|MEDIUM|HIGH, reasoning: str, error}.
    """
    default_response = {"pbn_risk": "UNKNOWN", "reasoning": "", "error": None}

    try:
        prompt_parts = [
            "You are an SEO analyst deciding whether a website is part of a Private Blog "
            "Network (PBN) or a link-selling farm, versus a genuine business site.",
            "",
            "## Computed signals",
            json.dumps(signals, indent=2, default=str),
            "",
            "## Heuristic observations",
            *(f"- {r}" for r in (heuristic_reasons or ["(none)"])),
        ]
        if ranking_sample:
            prompt_parts += ["", "## Sample of keywords the domain ranks for",
                             ", ".join(ranking_sample[:40])]
        if homepage_text:
            prompt_parts += ["", "## Homepage content (what the site claims to be)", homepage_text]
        prompt_parts += [
            "",
            "## Task",
            "Weigh the signals. The strongest PBN tells are: backlinks with little or no organic "
            "audience, a topic mismatch between the homepage and what the domain ranks for "
            "(legacy/expired-domain content), link-network-style outbound linking, and a young "
            "domain with an outsized backlink profile. A real business with normal traffic and "
            "on-topic content is NOT a PBN even if it has many backlinks.",
            "A strange, unrelated site that links BACK to the audited domain "
            "(`reciprocal_strange_link_count` > 0) is a strong deliberate-link-exchange "
            "signal — especially if those partners are not real businesses. Conversely, "
            "clear business-legitimacy signals (`business_is_legit` true) on the audited "
            "site weigh AGAINST a PBN verdict.",
            "",
            "CRITICAL — do not over-read keyword noise: a SMALL number of gambling/adult keyword "
            "hits relative to the site's total keywords is NOISE, usually incidental whole-word "
            "matches in business or place names (e.g. 'casino' in the taxi firm 'Casino Cab Co', "
            "or unrelated location names). Check `porn_gambling_keyword_ratio` / "
            "`total_ranked_keywords`: only treat gambling/adult ranking as a real signal when it "
            "is a SUBSTANTIAL share of the site's keywords, or when actual gambling/adult content "
            "or outbound links are present. A few hits among hundreds or thousands of keywords "
            "should NOT raise the verdict.",
            "",
            "Return a JSON object with EXACTLY these keys:",
            "  - `pbn_risk`: one of LOW / MEDIUM / HIGH",
            "  - `reasoning`: 2-4 sentences citing the specific signals that drove the verdict",
            "",
            "Return ONLY the JSON object, no markdown fences, no extra text.",
        ]

        system = (
            "You are a meticulous SEO link-network analyst. "
            "You always respond with valid JSON only."
        )
        raw = _backend.chat_json(system, "\n".join(prompt_parts), max_tokens=500)
        parsed = _parse_json(raw)
        return {**default_response, **parsed, "error": None}

    except json.JSONDecodeError as exc:
        return {**default_response, "error": f"Failed to parse JSON: {exc}"}
    except ValueError as exc:
        return {**default_response, "error": str(exc)}
    except Exception as exc:
        logger.exception("Unexpected error in PBN assessment")
        return {**default_response, "error": str(exc)}

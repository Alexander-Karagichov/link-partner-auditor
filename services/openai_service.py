"""
OpenAI analysis service.

Takes the fully-assembled audit result for a domain and asks GPT to:
  1. Summarise the key findings.
  2. Assign a risk level: CLEAN / LOW / MEDIUM / HIGH / CRITICAL.
  3. Provide a short actionable recommendation.

The response is structured JSON to make UI rendering easy.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from openai import OpenAI, APIError, APIConnectionError, RateLimitError

from config import settings

logger = logging.getLogger(__name__)

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is not configured.")
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


# ── Prompt builder ─────────────────────────────────────────────────────────────

def _build_prompt(audit_data: dict, about_page_text: str = "") -> str:
    """
    Build the user message sent to the model.

    audit_data is the dict produced by audit_engine.py; it contains all
    SEMrush metrics, link findings, SERP results, and keyword matches.
    about_page_text is the scraped about-page content (passed separately,
    not stored in the exported audit data).
    """
    domain = audit_data.get("domain", "unknown")

    about_section = (
        ["## About Page (scraped for niche context – not in export)", about_page_text, ""]
        if about_page_text
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
        *about_section,
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
        "Return ONLY the JSON object, no markdown fences, no extra text.",
    ]
    return "\n".join(sections)


# ── Public API ─────────────────────────────────────────────────────────────────

def analyze_audit(audit_data: dict, about_page_text: str = "") -> dict:
    """
    Send audit_data to OpenAI and return a structured analysis dict.

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
        client = _get_client()
        prompt = _build_prompt(audit_data, about_page_text=about_page_text)

        completion = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a senior SEO and brand-safety analyst. "
                        "You always respond with valid JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_completion_tokens=800,
            response_format={"type": "json_object"},
        )

        raw = completion.choices[0].message.content or "{}"
        parsed = json.loads(raw)

        # Merge with defaults so callers always get expected keys
        return {**default_response, **parsed, "error": None}

    except (RateLimitError, APIConnectionError, APIError) as exc:
        msg = f"OpenAI API error: {exc}"
        logger.warning(msg)
        return {**default_response, "error": msg}
    except json.JSONDecodeError as exc:
        msg = f"Failed to parse OpenAI JSON response: {exc}"
        logger.warning(msg)
        return {**default_response, "error": msg}
    except ValueError as exc:
        return {**default_response, "error": str(exc)}
    except Exception as exc:
        logger.exception("Unexpected error in OpenAI analysis")
        return {**default_response, "error": str(exc)}


# ── Link building recommendation ───────────────────────────────────────────────

def recommend_link_building(audit_data: dict, linkbuilding_targets: list[dict], target_domain: str, about_page_text: str = "") -> dict:
    """
    Given the full audit result plus a list of {keyword, url} link-building
    targets, ask GPT to recommend:
      - best_keyword: which keyword to use as anchor text
      - target_url: the matching URL from the targets list (not hallucinated)
      - guest_post_topic: a relevant topic the webmaster could write about
      - reasoning: short explanation of why this keyword/page fits this site
    """
    default_response = {
        "best_keyword": None,
        "target_url": None,
        "guest_post_topic": None,
        "reasoning": None,
        "error": None,
    }

    try:
        client = _get_client()
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
            *(["", "## About Page (niche context)", about_page_text] if about_page_text else []),
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

        completion = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert SEO link-building strategist. You always respond with valid JSON only.",
                },
                {"role": "user", "content": "\n".join(prompt_parts)},
            ],
            temperature=0.3,
            max_completion_tokens=400,
            response_format={"type": "json_object"},
        )

        raw = completion.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        return {**default_response, **parsed, "error": None}

    except (RateLimitError, APIConnectionError, APIError) as exc:
        msg = f"OpenAI API error: {exc}"
        logger.warning(msg)
        return {**default_response, "error": msg}
    except json.JSONDecodeError as exc:
        return {**default_response, "error": f"Failed to parse JSON: {exc}"}
    except ValueError as exc:
        return {**default_response, "error": str(exc)}
    except Exception as exc:
        logger.exception("Unexpected error in link building recommendation")
        return {**default_response, "error": str(exc)}


# ── Outbound link gambling/porn classifier ─────────────────────────────────────

def classify_outbound_links(page_url: str, external_links: list[str]) -> list[dict]:
    """
    Ask GPT to classify a list of external links from a page body as
    gambling/adult or safe.

    Returns a list of dicts:
      {found_href, is_gambling_or_porn, category, reason}
    for every link that is classified as gambling or adult content.
    Only flagged links are returned (safe links are omitted).
    """
    if not external_links:
        return []

    default_response: list[dict] = []

    try:
        client = _get_client()

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

        completion = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You are a brand-safety content classifier. You always respond with valid JSON only.",
                },
                {"role": "user", "content": "\n".join(prompt_parts)},
            ],
            temperature=0.1,
            max_completion_tokens=800,
            response_format={"type": "json_object"},
        )

        raw = completion.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        return parsed.get("flagged_links", [])

    except (RateLimitError, APIConnectionError, APIError) as exc:
        logger.warning("OpenAI API error classifying outbound links: %s", exc)
        return default_response
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse OpenAI JSON for outbound link classification: %s", exc)
        return default_response
    except ValueError as exc:
        logger.warning("ValueError classifying outbound links: %s", exc)
        return default_response
    except Exception as exc:
        logger.exception("Unexpected error classifying outbound links")
        return default_response

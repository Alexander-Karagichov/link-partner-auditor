"""
Anthropic (Claude) backend for the LLM service.

Mirrors the OpenAI backend's `chat_json` signature so the two are
interchangeable behind `llm_service.py`, which selects this backend when
LLM_PROVIDER=anthropic. Uses the official Anthropic SDK.
"""

from __future__ import annotations

from typing import Optional

import anthropic

from config import settings

_client: "Optional[anthropic.Anthropic]" = None


def _get_client() -> "anthropic.Anthropic":
    global _client
    if _client is None:
        if not settings.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY is not configured.")
        _client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


def chat_json(system_prompt: str, user_prompt: str, max_tokens: int = 800) -> str:
    """
    Send a system + user prompt to Claude and return the raw response text.

    Sampling params (`temperature`/`top_p`/`top_k`) are intentionally omitted —
    Opus 4.x models reject them. These are short JSON classification calls, so
    extended thinking and streaming aren't needed. The prompt instructs
    JSON-only output; `llm_service` strips any stray markdown fences before
    parsing.
    """
    client = _get_client()
    response = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(b.text for b in response.content if b.type == "text") or "{}"

"""
OpenAI backend for the LLM service.

Exposes a single `chat_json` function that sends a system + user prompt and
returns the model's raw text response (expected to be JSON). All provider-
agnostic prompt building, JSON parsing, and orchestration live in
`llm_service.py`, which selects this backend when LLM_PROVIDER=openai.
"""

from __future__ import annotations

from typing import Optional

from openai import OpenAI

from config import settings

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is not configured.")
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


def chat_json(system_prompt: str, user_prompt: str, max_tokens: int = 800) -> str:
    """
    Send a system + user prompt to OpenAI and return the raw response text.

    `temperature` is intentionally omitted: GPT-5 reasoning models (e.g.
    gpt-5.2) only accept the default temperature and return a 400 on any
    custom value. `response_format=json_object` keeps the output valid JSON,
    and `max_completion_tokens` (not the legacy `max_tokens`) is what these
    models expect.
    """
    client = _get_client()
    completion = client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_completion_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    return completion.choices[0].message.content or "{}"

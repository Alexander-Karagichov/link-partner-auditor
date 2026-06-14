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

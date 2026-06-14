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

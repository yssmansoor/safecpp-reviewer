"""Scoring functions for eval results.

The combined score is:

    score = 1.0 if verified else 0.0
          + 0.3 * similarity_to_expected_fix
          + 0.2 if any expected keyword appears in the explanation
          else 0.0

Maximum is 1.5.  A perfectly-verified fix that exactly matches the expected
output and uses the right vocabulary scores 1.5.  A verified fix that takes
a different valid approach scores 1.0.  Promising but broken output scores
in the 0.0-0.5 range — partial credit for being on the right track.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from safecpp_reviewer.eval.models import EvalCase


def normalized_similarity(a: str, b: str) -> float:
    """Return a 0.0-1.0 similarity between two strings using SequenceMatcher.

    Both inputs are normalized: whitespace collapsed, fences stripped.
    Returns 0.0 if either input is empty.
    """
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _normalize(s: str) -> str:
    """Strip code fences and collapse whitespace for fair comparison."""
    s = re.sub(r"```(?:cpp|c\+\+)?\s*\n?", "", s)
    s = re.sub(r"```\s*", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def keyword_match(explanation: str | None, keywords: list[str]) -> bool:
    """True if any of *keywords* (case-insensitive) appears in *explanation*."""
    if not explanation or not keywords:
        return False
    text = explanation.lower()
    return any(kw.lower() in text for kw in keywords)


def compute_score(
    *,
    verified: bool,
    similarity: float,
    keyword_match_flag: bool,
) -> float:
    """Combine the three signals into a single score (0.0 to 1.5)."""
    score = 0.0
    if verified:
        score += 1.0
    score += 0.3 * similarity
    if keyword_match_flag:
        score += 0.2
    return round(score, 4)


def score_case(
    case: EvalCase,
    llm_fix: str | None,
    llm_explanation: str | None,
    verified: bool,
) -> tuple[float, float, bool]:
    """Return ``(score, similarity, keyword_match)`` for one case.

    Convenience wrapper around the three primitives above so callers can
    score a single case in one call.
    """
    sim = normalized_similarity(llm_fix or "", case.expected_fix)
    kw = keyword_match(llm_explanation, case.expected_explanation_keywords)
    score = compute_score(verified=verified, similarity=sim, keyword_match_flag=kw)
    return score, sim, kw

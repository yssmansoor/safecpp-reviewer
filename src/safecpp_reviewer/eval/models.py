"""Pydantic models for the eval harness."""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from pydantic import BaseModel, Field


class EvalCase(BaseModel):
    """A single hand-authored test case for fix-quality evaluation.

    Each case represents a known violation paired with a canonical correct
    fix.  The harness feeds *bad_code* through the LLM and scores its
    response against *expected_fix* and *expected_explanation_keywords*.
    """

    case_id: str
    rule_id: str
    description: str
    bad_code: str
    expected_fix: str
    expected_explanation_keywords: list[str] = Field(default_factory=list)
    # If set, the case is for a specific severity level — defaults to "warning"
    severity: str = "warning"


class EvalResult(BaseModel):
    """Outcome of running a single :class:`EvalCase` through an LLM."""

    case_id: str
    rule_id: str

    # LLM output
    llm_explanation: str | None = None
    llm_fix: str | None = None

    # Component scores
    verified: bool = False  # clang-tidy says the violation is gone
    similarity: float = 0.0  # 0.0-1.0, normalized Levenshtein vs expected
    keyword_match: bool = False  # at least one expected keyword in explanation

    # Combined score (max 1.5):
    #   1.0 if verified
    #   0.3 * similarity (bonus for matching expected fix)
    #   0.2 if keyword_match
    score: float = 0.0

    elapsed_seconds: float = 0.0
    error: str | None = None


class EvalSummary(BaseModel):
    """Aggregate report across many :class:`EvalResult` objects."""

    model: str  # e.g. "qwen2.5-coder-7b"
    server_url: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

    total_cases: int
    verified_count: int
    keyword_match_count: int
    mean_similarity: float
    mean_score: float
    pass_rate: float  # = verified_count / total_cases

    by_rule: dict[str, RuleStats] = Field(default_factory=dict)
    results: list[EvalResult] = Field(default_factory=list)

    @classmethod
    def from_results(cls, results: list[EvalResult], model: str, server_url: str) -> EvalSummary:
        n = len(results) or 1
        verified = sum(1 for r in results if r.verified)
        kw = sum(1 for r in results if r.keyword_match)
        mean_sim = sum(r.similarity for r in results) / n
        mean_score = sum(r.score for r in results) / n
        pass_rate = verified / n

        by_rule: dict[str, RuleStats] = {}
        rule_counter = Counter(r.rule_id for r in results)
        for rule_id, count in rule_counter.items():
            rule_results = [r for r in results if r.rule_id == rule_id]
            r_verified = sum(1 for r in rule_results if r.verified)
            r_score = sum(r.score for r in rule_results) / count
            by_rule[rule_id] = RuleStats(
                rule_id=rule_id,
                total=count,
                verified=r_verified,
                pass_rate=r_verified / count,
                mean_score=r_score,
            )

        return cls(
            model=model,
            server_url=server_url,
            total_cases=len(results),
            verified_count=verified,
            keyword_match_count=kw,
            mean_similarity=mean_sim,
            mean_score=mean_score,
            pass_rate=pass_rate,
            by_rule=by_rule,
            results=results,
        )


class RuleStats(BaseModel):
    """Per-rule aggregate statistics."""

    rule_id: str
    total: int
    verified: int
    pass_rate: float
    mean_score: float

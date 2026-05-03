"""Ground-truth eval harness for safecpp-reviewer."""

from safecpp_reviewer.eval.dataset import load_cases
from safecpp_reviewer.eval.harness import EvalHarness
from safecpp_reviewer.eval.models import EvalCase, EvalResult, EvalSummary, RuleStats
from safecpp_reviewer.eval.scorer import compute_score, normalized_similarity, score_case

__all__ = [
    "EvalCase",
    "EvalResult",
    "EvalSummary",
    "EvalHarness",
    "RuleStats",
    "compute_score",
    "load_cases",
    "normalized_similarity",
    "score_case",
]

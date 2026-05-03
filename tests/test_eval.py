"""Tests for the eval harness."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from safecpp_reviewer.agent.models import VerificationResult
from safecpp_reviewer.eval.dataset import load_cases
from safecpp_reviewer.eval.harness import EvalHarness
from safecpp_reviewer.eval.models import EvalCase, EvalResult, EvalSummary
from safecpp_reviewer.eval.scorer import (
    compute_score,
    keyword_match,
    normalized_similarity,
)

# ---------------------------------------------------------------------------
# Scoring primitives
# ---------------------------------------------------------------------------


def test_normalized_similarity_identical() -> None:
    assert normalized_similarity("int x = 0;", "int x = 0;") == 1.0


def test_normalized_similarity_ignores_fences() -> None:
    sim = normalized_similarity(
        "```cpp\nint x = 0;\n```",
        "int x = 0;",
    )
    assert sim > 0.95


def test_normalized_similarity_zero_on_empty() -> None:
    assert normalized_similarity("", "x") == 0.0
    assert normalized_similarity("x", "") == 0.0


def test_keyword_match_case_insensitive() -> None:
    assert keyword_match("Use STATIC_CAST instead", ["static_cast"])
    assert keyword_match("Try a named cast", ["static_cast", "named cast"])


def test_keyword_match_no_keywords_returns_false() -> None:
    assert not keyword_match("anything", [])


def test_keyword_match_no_explanation_returns_false() -> None:
    assert not keyword_match(None, ["foo"])


def test_compute_score_max_when_everything_aligns() -> None:
    s = compute_score(verified=True, similarity=1.0, keyword_match_flag=True)
    assert s == 1.5


def test_compute_score_zero_when_nothing_aligns() -> None:
    s = compute_score(verified=False, similarity=0.0, keyword_match_flag=False)
    assert s == 0.0


def test_compute_score_partial_credit() -> None:
    # Not verified but similar fix and right keyword
    s = compute_score(verified=False, similarity=0.9, keyword_match_flag=True)
    # 0 + 0.27 + 0.2 = 0.47
    assert 0.4 < s < 0.5


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------


def test_load_cases_default_includes_bundled_data() -> None:
    cases = load_cases()
    assert len(cases) > 0
    # We hand-authored at least one of these
    rule_ids = {c.rule_id for c in cases}
    assert any("cppcoreguidelines" in r for r in rule_ids)


def test_load_cases_handles_list_form(tmp_path: Path) -> None:
    (tmp_path / "x.yaml").write_text(
        "- case_id: t1\n"
        "  rule_id: r1\n"
        "  description: d\n"
        "  bad_code: 'int x;'\n"
        "  expected_fix: 'int x = 0;'\n"
    )
    cases = load_cases(tmp_path)
    assert len(cases) == 1
    assert cases[0].case_id == "t1"


def test_load_cases_handles_cases_key(tmp_path: Path) -> None:
    (tmp_path / "x.yaml").write_text(
        "cases:\n"
        "  - case_id: t1\n"
        "    rule_id: r1\n"
        "    description: d\n"
        "    bad_code: 'int x;'\n"
        "    expected_fix: 'int x = 0;'\n"
    )
    cases = load_cases(tmp_path)
    assert len(cases) == 1


def test_load_cases_skips_invalid_yaml(tmp_path: Path) -> None:
    (tmp_path / "good.yaml").write_text(
        "cases:\n"
        "  - case_id: ok\n"
        "    rule_id: r\n"
        "    description: d\n"
        "    bad_code: 'x'\n"
        "    expected_fix: 'y'\n"
    )
    (tmp_path / "bad.yaml").write_text("this: is: not: valid: yaml: [")
    cases = load_cases(tmp_path)
    assert len(cases) == 1


# ---------------------------------------------------------------------------
# EvalSummary aggregation
# ---------------------------------------------------------------------------


def test_summary_aggregates_pass_rate() -> None:
    results = [
        EvalResult(case_id="a", rule_id="r1", verified=True, score=1.0),
        EvalResult(case_id="b", rule_id="r1", verified=False, score=0.0),
        EvalResult(case_id="c", rule_id="r2", verified=True, score=1.5),
    ]
    summary = EvalSummary.from_results(results, model="m", server_url="u")
    assert summary.verified_count == 2
    assert abs(summary.pass_rate - 2 / 3) < 0.01
    assert "r1" in summary.by_rule
    assert summary.by_rule["r1"].pass_rate == 0.5


def test_summary_handles_empty_results() -> None:
    summary = EvalSummary.from_results([], model="m", server_url="u")
    assert summary.total_cases == 0
    assert summary.pass_rate == 0.0


# ---------------------------------------------------------------------------
# EvalHarness with mocked reviewer
# ---------------------------------------------------------------------------


def _make_case() -> EvalCase:
    return EvalCase(
        case_id="t1",
        rule_id="clang-tidy:cppcoreguidelines-pro-type-cstyle-cast",
        description="C-style cast",
        bad_code="int f(int a) { return (int)a; }",
        expected_fix="int f(int a) { return static_cast<int>(a); }",
        expected_explanation_keywords=["static_cast"],
    )


def test_harness_records_score_when_reviewer_succeeds() -> None:
    reviewer = MagicMock()
    reviewer.client.base_url = "http://test"

    def fake_review(v: object) -> object:
        v.fix_suggestion = (  # type: ignore[attr-defined]
            "Use static_cast for explicit named casts.\n\n"
            "```cpp\nint f(int a) { return static_cast<int>(a); }\n```"
        )
        return v

    reviewer.review.side_effect = fake_review

    verifier = MagicMock()
    verifier.verify.return_value = VerificationResult(resolved=True)

    harness = EvalHarness(reviewer=reviewer, verifier=verifier)
    summary = harness.run([_make_case()], model_name="test")

    assert summary.total_cases == 1
    assert summary.verified_count == 1
    assert summary.results[0].keyword_match is True
    # Verified + similar + keyword → high score
    assert summary.results[0].score > 1.0


def test_harness_records_error_when_reviewer_raises() -> None:
    reviewer = MagicMock()
    reviewer.client.base_url = "http://test"
    reviewer.review.side_effect = RuntimeError("boom")

    verifier = MagicMock()

    harness = EvalHarness(reviewer=reviewer, verifier=verifier)
    summary = harness.run([_make_case()], model_name="test")

    assert summary.results[0].error is not None
    assert summary.verified_count == 0

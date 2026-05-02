from pathlib import Path

import pytest

from safecpp_reviewer.agent.reviewer import ViolationReviewer
from safecpp_reviewer.analyzer import _review_by_chunk
from safecpp_reviewer.analyzer.models import Violation
from safecpp_reviewer.chunker.models import Chunk
from safecpp_reviewer.knowledge import Rule, RuleStore
from safecpp_reviewer.llm.client import CompletionResult


class FakeClient:
    """A fake LLM client for testing purposes."""

    def __init__(self, text: str | None = None) -> None:
        self.prompts: list[str] = []
        self.text = text or (
            '{"reviews":['
            '{"violation_index":0,"explanation":"Fix first.","fixed_code":"int first = 1;"},'
            '{"violation_index":1,"explanation":"Fix second.","fixed_code":"int second = 2;"}'
            "]}"
        )

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        """A fake completion method for testing."""
        self.prompts.append(prompt)
        return CompletionResult(
            text=self.text,
            tokens_generated=8,
            tokens_per_second=0.0,
            prompt_tokens=10,
        )


class FakeBatchReviewer:
    """A fake batch reviewer for testing purposes."""

    def __init__(self) -> None:
        self.chunk_calls: list[tuple[str | None, list[int]]] = []
        self.single_calls: list[int] = []

    def review_chunk(self, chunk: Chunk, violations: list[Violation]) -> list[Violation]:
        """Review a chunk of code with multiple violations."""
        self.chunk_calls.append((chunk.name, [violation.line for violation in violations]))
        for violation in violations:
            violation.fix_suggestion = f"chunk:{chunk.name}"
        return violations

    def review(self, violation: Violation) -> Violation:
        """Review a single violation."""
        self.single_calls.append(violation.line)
        violation.fix_suggestion = "single"
        return violation


def _violation(line: int, rule_id: str = "test:rule") -> Violation:
    return Violation(
        file=Path("tests/fixtures/sample_violations.cpp"),
        line=line,
        rule_id=rule_id,
        severity="warning",
        message="test violation",
        tool="clang-tidy",
    )


def test_review_chunk_uses_one_llm_call_for_multiple_violations() -> None:
    """Test that review_chunk uses one LLM call for multiple violations."""
    client = FakeClient()
    reviewer = ViolationReviewer(client)  # type: ignore[arg-type]
    chunk = Chunk(
        file=Path("sample.cpp"),
        start_line=10,
        end_line=18,
        content="int process(int a, int b) { return a + b; }",
        chunk_type="function",
        name="process",
        token_estimate=10,
    )
    violations = [_violation(11, "rule:a"), _violation(12, "rule:b")]

    reviewed = reviewer.review_chunk(chunk, violations)

    assert reviewed == violations
    assert len(client.prompts) == 1
    assert "rule:a" in client.prompts[0]
    assert "rule:b" in client.prompts[0]
    assert "Fix first." in (violations[0].fix_suggestion or "")
    assert "int first = 1;" in (violations[0].fix_suggestion or "")
    assert "Fix second." in (violations[1].fix_suggestion or "")
    assert "int second = 2;" in (violations[1].fix_suggestion or "")


def test_review_chunk_prompt_includes_rule_store_guidance() -> None:
    """Test that the review chunk prompt includes rule store guidance."""
    client = FakeClient()
    rule_store = RuleStore(
        [
            Rule(
                rule_id="rule:a",
                short_name="Avoid test rule",
                category="test",
                rationale="Use the safer pattern.",
                bad_example="int* p = NULL;",
                good_example="int* p = nullptr;",
            )
        ]
    )
    reviewer = ViolationReviewer(client, rule_store=rule_store)  # type: ignore[arg-type]
    chunk = Chunk(
        file=Path("sample.cpp"),
        start_line=10,
        end_line=18,
        content="int process(int a, int b) { return a + b; }",
        chunk_type="function",
        name="process",
        token_estimate=10,
    )

    reviewer.review_chunk(chunk, [_violation(11, "rule:a"), _violation(12, "rule:missing")])

    assert len(client.prompts) == 1
    assert "Rule guidance:" in client.prompts[0]
    assert "rule:a - Avoid test rule" in client.prompts[0]
    assert "Use the safer pattern." in client.prompts[0]
    assert "rule:missing - " not in client.prompts[0]


def test_review_chunk_accepts_one_based_index_when_model_returns_one_for_single_violation() -> None:
    """Test that review_chunk accepts a one-based index."""
    client = FakeClient(
        '{"reviews":[{"violation_index":1,"explanation":"Fix only.","fixed_code":"int only = 1;"}]}'
    )
    reviewer = ViolationReviewer(client)  # type: ignore[arg-type]
    chunk = Chunk(
        file=Path("sample.cpp"),
        start_line=24,
        end_line=25,
        content="// leading comments",
        chunk_type="global",
        name=None,
        token_estimate=4,
    )
    violations = [_violation(25, "rule:only")]

    reviewed = reviewer.review_chunk(chunk, violations)

    assert reviewed == violations
    assert "Fix only." in (violations[0].fix_suggestion or "")
    assert "int only = 1;" in (violations[0].fix_suggestion or "")


def test_review_chunk_skips_duplicate_extra_review_for_single_violation_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that review_chunk skips duplicate extra reviews for a single violation."""
    client = FakeClient(
        '{"reviews":['
        '{"violation_index":0,"explanation":"Fix only.","fixed_code":"int only = 1;"},'
        '{"violation_index":1,"explanation":"Duplicate.","fixed_code":"int duplicate = 2;"}'
        "]}"
    )
    reviewer = ViolationReviewer(client)  # type: ignore[arg-type]
    chunk = Chunk(
        file=Path("tests/fixtures/sample_violations.cpp"),
        start_line=24,
        end_line=25,
        content="// [modernize-use-override] missing override keyword",
        chunk_type="global",
        name=None,
        token_estimate=12,
    )
    violations = [_violation(25, "rule:only")]

    with caplog.at_level("WARNING"):
        reviewed = reviewer.review_chunk(chunk, violations)

    assert reviewed == violations
    assert "Fix only." in (violations[0].fix_suggestion or "")
    assert "invalid violation_index" not in caplog.text


def test_review_by_chunk_batches_violations_by_containing_chunk() -> None:
    """Test that _review_by_chunk batches violations by their containing chunk."""
    reviewer = FakeBatchReviewer()
    violations = [_violation(11, "rule:a"), _violation(12, "rule:b"), _violation(22, "rule:c")]

    reviewed = _review_by_chunk(
        Path("tests/fixtures/sample_violations.cpp"),
        violations,
        reviewer,  # type: ignore[arg-type]
    )

    assert reviewed == violations
    assert reviewer.chunk_calls == [("process", [11, 12]), ("magic", [22])]
    assert not reviewer.single_calls
    assert [violation.fix_suggestion for violation in violations] == [
        "chunk:process",
        "chunk:process",
        "chunk:magic",
    ]


def test_review_by_chunk_falls_back_to_individual_review_for_many_violations() -> None:
    """Test that _review_by_chunk falls back to individual review when a chunk has too many violations."""
    reviewer = FakeBatchReviewer()
    violations = [_violation(11, f"rule:{index}") for index in range(6)]

    reviewed = _review_by_chunk(
        Path("tests/fixtures/sample_violations.cpp"),
        violations,
        reviewer,  # type: ignore[arg-type]
    )

    assert reviewed == violations
    assert not reviewer.chunk_calls
    assert reviewer.single_calls == [11, 11, 11, 11, 11, 11]
    assert [violation.fix_suggestion for violation in violations] == ["single"] * 6

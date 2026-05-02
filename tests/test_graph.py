"""Tests for the LangGraph review pipeline.

Three layers:
    1. Unit tests for individual node factories (no graph involved).
    2. Conditional-edge tests for ``should_retry``.
    3. End-to-end graph invocations with mocked dependencies.

All tests run without a live llama.cpp server or real clang-tidy/cppcheck —
heavy components are replaced via monkeypatch or fake objects.
"""

from __future__ import annotations

import typing
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from safecpp_reviewer.agent.graph import build_graph, initial_state
from safecpp_reviewer.agent.nodes import (
    chunk_node,
    make_analyze_node,
    make_retry_node,
    make_review_node,
    should_retry,
)
from safecpp_reviewer.agent.state import ReviewerState
from safecpp_reviewer.analyzer.models import Violation
from safecpp_reviewer.chunker.models import Chunk

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_violation(
    line: int = 10,
    rule_id: str = "clang-tidy:test-rule",
    file: Path = Path("/tmp/sample.cpp"),
    fix_suggestion: str | None = None,
) -> Violation:
    return Violation(
        file=file,
        line=line,
        column=1,
        rule_id=rule_id,
        severity="warning",
        message="test violation",
        tool="clang-tidy",
        fix_suggestion=fix_suggestion,
    )


def _make_chunk(
    name: str = "process",
    start: int = 5,
    end: int = 20,
    file: Path = Path("/tmp/sample.cpp"),
) -> Chunk:
    return Chunk(
        file=file,
        start_line=start,
        end_line=end,
        content=f"// chunk {name}\nint {name}() {{ return 0; }}",
        chunk_type="function",
        name=name,
        token_estimate=20,
    )


def _empty_state(source: Path = Path("/tmp/sample.cpp")) -> ReviewerState:
    return ReviewerState(
        source_file=source,
        violations=[],
        chunks=[],
        failed_reviews=[],
        retry_count=0,
    )


# ---------------------------------------------------------------------------
# analyze_node
# ---------------------------------------------------------------------------


def test_analyze_node_collects_violations_from_both_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src: typing.Final = tmp_path / "x.cpp"
    src.write_text("int main() { return 0; }\n")

    ct_violations: typing.Final = [_make_violation(line=1, rule_id="clang-tidy:foo", file=src)]
    cc_violations: typing.Final = [_make_violation(line=2, rule_id="cppcheck:bar", file=src)]
    cc_violations[0].tool = "cppcheck"  # type: ignore[assignment]

    monkeypatch.setattr(
        "safecpp_reviewer.agent.nodes.ClangTidyRunner.run",
        lambda self, f: ct_violations,
    )
    monkeypatch.setattr(
        "safecpp_reviewer.agent.nodes.CppcheckRunner.run",
        lambda self, f: cc_violations,
    )
    monkeypatch.setattr("safecpp_reviewer.agent.nodes.extract_snippet", lambda v: None)

    node: typing.Final = make_analyze_node()
    out: typing.Final = node(
        {"source_file": src, "violations": [], "chunks": [], "failed_reviews": [], "retry_count": 0}
    )

    assert len(out["violations"]) == 2
    rule_ids: typing.Final = {v.rule_id for v in out["violations"]}
    assert rule_ids == {"clang-tidy:foo", "cppcheck:bar"}


def test_analyze_node_deduplicates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src: typing.Final = tmp_path / "x.cpp"
    src.write_text("int x;\n")

    dup: typing.Final = _make_violation(line=1, rule_id="clang-tidy:foo", file=src)

    monkeypatch.setattr(
        "safecpp_reviewer.agent.nodes.ClangTidyRunner.run",
        lambda self, f: [dup, dup, dup],
    )
    monkeypatch.setattr("safecpp_reviewer.agent.nodes.CppcheckRunner.run", lambda self, f: [])
    monkeypatch.setattr("safecpp_reviewer.agent.nodes.extract_snippet", lambda v: None)

    node: typing.Final = make_analyze_node()
    out: typing.Final = node(
        {"source_file": src, "violations": [], "chunks": [], "failed_reviews": [], "retry_count": 0}
    )

    assert len(out["violations"]) == 1


def test_analyze_node_continues_when_clang_tidy_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src: typing.Final = tmp_path / "x.cpp"
    src.write_text("int x;\n")

    def boom(self: Any, f: Path) -> list[Violation]:
        raise RuntimeError("clang-tidy missing")

    monkeypatch.setattr("safecpp_reviewer.agent.nodes.ClangTidyRunner.run", boom)
    monkeypatch.setattr(
        "safecpp_reviewer.agent.nodes.CppcheckRunner.run",
        lambda self, f: [_make_violation(line=1, file=src)],
    )
    monkeypatch.setattr("safecpp_reviewer.agent.nodes.extract_snippet", lambda v: None)

    node: typing.Final = make_analyze_node()
    out: typing.Final = node(
        {"source_file": src, "violations": [], "chunks": [], "failed_reviews": [], "retry_count": 0}
    )

    # Should still get the cppcheck result back.
    assert len(out["violations"]) == 1


# ---------------------------------------------------------------------------
# chunk_node
# ---------------------------------------------------------------------------


def test_chunk_node_calls_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks: typing.Final = [_make_chunk("a"), _make_chunk("b", start=30, end=40)]
    monkeypatch.setattr("safecpp_reviewer.agent.nodes.parse_chunks", lambda f: chunks)

    out: typing.Final = chunk_node(_empty_state())
    assert out["chunks"] == chunks


def test_chunk_node_returns_empty_on_parse_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_: Any) -> list[Chunk]:
        raise RuntimeError("tree-sitter exploded")

    monkeypatch.setattr("safecpp_reviewer.agent.nodes.parse_chunks", boom)

    out: typing.Final = chunk_node(_empty_state())
    assert out["chunks"] == []


# ---------------------------------------------------------------------------
# review_node
# ---------------------------------------------------------------------------


def test_review_node_no_op_when_reviewer_is_none() -> None:
    node: typing.Final = make_review_node(reviewer=None)
    state: typing.Final = _empty_state()
    state["violations"] = [_make_violation()]

    out: typing.Final = node(state)
    assert out["failed_reviews"] == []


def test_review_node_falls_back_to_individual_when_no_chunks() -> None:
    reviewer: typing.Final = MagicMock()

    # Reviewer "succeeds" by setting fix_suggestion
    def fake_review(v: Violation) -> Violation:
        v.fix_suggestion = "fix"
        return v

    reviewer.review.side_effect = fake_review

    node: typing.Final = make_review_node(reviewer=reviewer)
    state: typing.Final = _empty_state()
    state["violations"] = [_make_violation(line=10), _make_violation(line=20)]

    out: typing.Final = node(state)

    assert reviewer.review.call_count == 2
    reviewer.review_chunk.assert_not_called()
    assert out["failed_reviews"] == []


def test_review_node_batches_violations_in_same_chunk() -> None:
    reviewer: typing.Final = MagicMock()

    def fake_chunk_review(chunk: Chunk, vs: list[Violation]) -> list[Violation]:
        for v in vs:
            v.fix_suggestion = "fix"
        return vs

    reviewer.review_chunk.side_effect = fake_chunk_review

    chunk: typing.Final = _make_chunk("process", start=5, end=20)
    state: typing.Final = _empty_state()
    state["chunks"] = [chunk]
    state["violations"] = [
        _make_violation(line=10),
        _make_violation(line=15),
        _make_violation(line=18),
    ]

    node: typing.Final = make_review_node(reviewer=reviewer)
    out: typing.Final = node(state)

    # All three violations sit in the same chunk → exactly one batched call
    reviewer.review_chunk.assert_called_once()
    reviewer.review.assert_not_called()
    assert out["failed_reviews"] == []


def test_review_node_falls_back_when_batch_too_large() -> None:
    reviewer: typing.Final = MagicMock()
    reviewer.review.side_effect = lambda v: setattr(v, "fix_suggestion", "fix") or v

    chunk: typing.Final = _make_chunk(start=1, end=100)
    state: typing.Final = _empty_state()
    state["chunks"] = [chunk]
    state["violations"] = [_make_violation(line=i) for i in range(1, 8)]  # 7 > 5

    node: typing.Final = make_review_node(reviewer=reviewer, max_batch_size=5)
    node(state)

    reviewer.review_chunk.assert_not_called()
    assert reviewer.review.call_count == 7


def test_review_node_records_failures() -> None:
    reviewer: typing.Final = MagicMock()
    # Reviewer never sets fix_suggestion → counts as failure
    reviewer.review.side_effect = lambda v: v

    state: typing.Final = _empty_state()
    state["violations"] = [_make_violation(line=10)]

    node: typing.Final = make_review_node(reviewer=reviewer)
    out: typing.Final = node(state)

    assert len(out["failed_reviews"]) == 1


def test_review_node_handles_orphan_violations() -> None:
    """Violations outside any chunk should fall through to individual review."""
    reviewer: typing.Final = MagicMock()
    reviewer.review.side_effect = lambda v: setattr(v, "fix_suggestion", "fix") or v
    reviewer.review_chunk.side_effect = lambda c, vs: vs

    chunk: typing.Final = _make_chunk(start=10, end=20)
    state: typing.Final = _empty_state()
    state["chunks"] = [chunk]
    state["violations"] = [
        _make_violation(line=15),  # in chunk
        _make_violation(line=100),  # orphan
    ]

    node: typing.Final = make_review_node(reviewer=reviewer)
    node(state)

    reviewer.review_chunk.assert_called_once()
    reviewer.review.assert_called_once()  # the orphan


# ---------------------------------------------------------------------------
# retry_node + should_retry
# ---------------------------------------------------------------------------


def test_should_retry_returns_done_when_no_failures() -> None:
    state: typing.Final = _empty_state()
    assert should_retry(state) == "done"


def test_should_retry_returns_retry_when_failures_under_cap() -> None:
    state: typing.Final = _empty_state()
    state["failed_reviews"] = [_make_violation()]
    state["retry_count"] = 0
    assert should_retry(state) == "retry"


def test_should_retry_returns_done_when_cap_reached() -> None:
    state: typing.Final = _empty_state()
    state["failed_reviews"] = [_make_violation()]
    state["retry_count"] = 2
    assert should_retry(state) == "done"


def test_retry_node_increments_count_and_re_reviews() -> None:
    reviewer: typing.Final = MagicMock()
    reviewer.review.side_effect = lambda v: setattr(v, "fix_suggestion", "fixed") or v

    failed: typing.Final = _make_violation()
    state: typing.Final = _empty_state()
    state["failed_reviews"] = [failed]
    state["retry_count"] = 0

    node: typing.Final = make_retry_node(reviewer=reviewer)
    out: typing.Final = node(state)

    reviewer.review.assert_called_once_with(failed)
    assert out["retry_count"] == 1
    assert out["failed_reviews"] == []  # succeeded on retry


def test_retry_node_no_op_when_reviewer_is_none() -> None:
    state: typing.Final = _empty_state()
    state["failed_reviews"] = [_make_violation()]

    node: typing.Final = make_retry_node(reviewer=None)
    out: typing.Final = node(state)

    assert out["retry_count"] == 0  # untouched


def test_retry_node_no_op_when_no_failures() -> None:
    reviewer: typing.Final = MagicMock()
    state: typing.Final = _empty_state()

    node: typing.Final = make_retry_node(reviewer=reviewer)
    node(state)

    reviewer.review.assert_not_called()


def test_retry_node_caps_at_max_retries() -> None:
    reviewer: typing.Final = MagicMock()

    state: typing.Final = _empty_state()
    state["failed_reviews"] = [_make_violation()]
    state["retry_count"] = 2  # already at max

    node: typing.Final = make_retry_node(reviewer=reviewer, max_retries=2)
    node(state)

    reviewer.review.assert_not_called()


# ---------------------------------------------------------------------------
# End-to-end graph
# ---------------------------------------------------------------------------


def test_graph_runs_end_to_end_with_mocked_deps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src: typing.Final = tmp_path / "x.cpp"
    src.write_text("int main() { return 0; }\n")

    monkeypatch.setattr(
        "safecpp_reviewer.agent.nodes.ClangTidyRunner.run",
        lambda self, f: [_make_violation(line=1, file=src)],
    )
    monkeypatch.setattr("safecpp_reviewer.agent.nodes.CppcheckRunner.run", lambda self, f: [])
    monkeypatch.setattr("safecpp_reviewer.agent.nodes.extract_snippet", lambda v: None)
    monkeypatch.setattr("safecpp_reviewer.agent.nodes.parse_chunks", lambda f: [])

    reviewer: typing.Final = MagicMock()
    reviewer.review.side_effect = lambda v: setattr(v, "fix_suggestion", "fixed") or v

    graph: typing.Final = build_graph(reviewer=reviewer)
    final: typing.Final = graph.invoke(initial_state(src))

    assert len(final["violations"]) == 1
    assert final["violations"][0].fix_suggestion == "fixed"
    assert final["failed_reviews"] == []


def test_graph_routes_through_retry_then_terminates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reviewer fails twice then succeeds — retry node fires, then terminates."""
    src: typing.Final = tmp_path / "x.cpp"
    src.write_text("int x;\n")

    v: typing.Final = _make_violation(line=1, file=src)

    monkeypatch.setattr("safecpp_reviewer.agent.nodes.ClangTidyRunner.run", lambda self, f: [v])
    monkeypatch.setattr("safecpp_reviewer.agent.nodes.CppcheckRunner.run", lambda self, f: [])
    monkeypatch.setattr("safecpp_reviewer.agent.nodes.extract_snippet", lambda v: None)
    monkeypatch.setattr("safecpp_reviewer.agent.nodes.parse_chunks", lambda f: [])

    # Reviewer fails on first call, succeeds on second.
    call_count: typing.Final = {"n": 0}

    def flaky_review(violation: Violation) -> Violation:
        call_count["n"] += 1
        if call_count["n"] >= 2:
            violation.fix_suggestion = "eventually fixed"
        return violation

    reviewer: typing.Final = MagicMock()
    reviewer.review.side_effect = flaky_review

    graph: typing.Final = build_graph(reviewer=reviewer)
    final: typing.Final = graph.invoke(initial_state(src))

    assert final["violations"][0].fix_suggestion == "eventually fixed"
    assert final["failed_reviews"] == []
    assert final["retry_count"] >= 1


def test_graph_skips_review_when_no_reviewer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src: typing.Final = tmp_path / "x.cpp"
    src.write_text("int x;\n")

    monkeypatch.setattr(
        "safecpp_reviewer.agent.nodes.ClangTidyRunner.run",
        lambda self, f: [_make_violation(line=1, file=src)],
    )
    monkeypatch.setattr("safecpp_reviewer.agent.nodes.CppcheckRunner.run", lambda self, f: [])
    monkeypatch.setattr("safecpp_reviewer.agent.nodes.extract_snippet", lambda v: None)
    monkeypatch.setattr("safecpp_reviewer.agent.nodes.parse_chunks", lambda f: [])

    graph: typing.Final = build_graph(reviewer=None)
    final: typing.Final = graph.invoke(initial_state(src))

    assert len(final["violations"]) == 1
    assert final["violations"][0].fix_suggestion is None
    assert final["failed_reviews"] == []

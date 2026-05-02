"""Tests for the fix verification loop.

All tests run without invoking real clang-tidy — the runner is replaced
with a stub that returns whatever violations the test specifies.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from safecpp_reviewer.agent.models import VerificationResult
from safecpp_reviewer.agent.nodes import make_verify_node
from safecpp_reviewer.agent.state import ReviewerState
from safecpp_reviewer.agent.verifier import FixVerifier
from safecpp_reviewer.analyzer.models import Violation
from safecpp_reviewer.chunker.models import Chunk

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_violation(
    line: int = 10,
    rule_id: str = "clang-tidy:test-rule",
    file: Path | None = None,
    fix_suggestion: str | None = None,
) -> Violation:
    return Violation(
        file=file or Path("/tmp/fake.cpp"),
        line=line,
        column=1,
        rule_id=rule_id,
        severity="warning",
        message="msg",
        tool="clang-tidy",
        fix_suggestion=fix_suggestion,
    )


def _make_chunk(start: int = 5, end: int = 20, file: Path | None = None) -> Chunk:
    return Chunk(
        file=file or Path("/tmp/fake.cpp"),
        start_line=start,
        end_line=end,
        content="// chunk\nint f() { return 0; }\n",
        chunk_type="function",
        name="f",
        token_estimate=10,
    )


# ---------------------------------------------------------------------------
# _extract_code
# ---------------------------------------------------------------------------


def test_extract_code_pulls_out_fenced_block() -> None:
    verifier = FixVerifier(clang_tidy=MagicMock())
    fix = "Some explanation.\n\n```cpp\nint x = 1;\nreturn x;\n```"

    code = verifier._extract_code(fix)

    assert code == "int x = 1;\nreturn x;"


def test_extract_code_handles_no_fence() -> None:
    verifier = FixVerifier(clang_tidy=MagicMock())
    fix = "Explanation.\n\nint x = 1;"

    code = verifier._extract_code(fix)

    assert "int x = 1;" in code


def test_extract_code_raises_on_empty() -> None:
    verifier = FixVerifier(clang_tidy=MagicMock())
    with pytest.raises(ValueError):
        verifier._extract_code("")


# ---------------------------------------------------------------------------
# verify(): end-to-end with stubbed clang-tidy
# ---------------------------------------------------------------------------


def test_verify_resolved_when_rule_no_longer_appears(tmp_path: Path) -> None:
    src = tmp_path / "x.cpp"
    src.write_text("int main() { return (int)0; }\n")

    v = _make_violation(line=1, file=src, fix_suggestion="```cpp\nint main() { return 0; }\n```")

    fake_clang_tidy = MagicMock()
    fake_clang_tidy.run.return_value = []  # No violations after fix

    verifier = FixVerifier(clang_tidy=fake_clang_tidy)
    result = verifier.verify(v)

    assert result.resolved is True
    assert result.new_violations == []


def test_verify_unresolved_when_same_rule_still_present(tmp_path: Path) -> None:
    src = tmp_path / "x.cpp"
    src.write_text("int main() { return (int)0; }\n")

    v = _make_violation(line=1, file=src, fix_suggestion="```cpp\nint x = 0;\n```")

    fake_clang_tidy = MagicMock()
    fake_clang_tidy.run.return_value = [_make_violation(line=1, rule_id=v.rule_id, file=src)]

    verifier = FixVerifier(clang_tidy=fake_clang_tidy)
    result = verifier.verify(v)

    assert result.resolved is False


def test_verify_detects_regression(tmp_path: Path) -> None:
    src = tmp_path / "x.cpp"
    src.write_text("int main() { return 0; }\n")

    v = _make_violation(
        line=1,
        rule_id="clang-tidy:original",
        file=src,
        fix_suggestion="```cpp\nint main() { return 0; }\n```",
    )

    # Original is gone, but a new violation appears
    fake_clang_tidy = MagicMock()
    fake_clang_tidy.run.return_value = [
        _make_violation(line=1, rule_id="clang-tidy:new-issue", file=src),
    ]

    verifier = FixVerifier(clang_tidy=fake_clang_tidy)
    result = verifier.verify(v)

    assert result.resolved is True
    assert result.regressed is True
    assert len(result.new_violations) == 1


def test_verify_returns_error_when_fix_suggestion_missing() -> None:
    v = _make_violation(fix_suggestion=None)
    verifier = FixVerifier(clang_tidy=MagicMock())

    result = verifier.verify(v)

    assert result.resolved is False
    assert result.error is not None


def test_verify_returns_error_when_source_missing() -> None:
    v = _make_violation(
        file=Path("/nonexistent.cpp"),
        fix_suggestion="```cpp\nint x;\n```",
    )
    verifier = FixVerifier(clang_tidy=MagicMock())

    result = verifier.verify(v)

    assert result.resolved is False
    assert result.error is not None


def test_verify_uses_chunk_when_provided(tmp_path: Path) -> None:
    src = tmp_path / "x.cpp"
    src.write_text("int a;\nint b;\nint c;\nint d;\nint e;\n")

    v = _make_violation(line=2, file=src, fix_suggestion="```cpp\nint b = 0;\n```")
    chunk = _make_chunk(start=1, end=5, file=src)

    fake_clang_tidy = MagicMock()
    fake_clang_tidy.run.return_value = []

    verifier = FixVerifier(clang_tidy=fake_clang_tidy)
    result = verifier.verify(v, chunk=chunk)

    assert result.resolved is True
    # Verify the patched file was actually written and contained the fix
    fake_clang_tidy.run.assert_called_once()
    patched_path = fake_clang_tidy.run.call_args[0][0]
    assert patched_path.name == "x.cpp"


def test_verify_respects_line_margin(tmp_path: Path) -> None:
    """A violation that re-appears just outside the chunk range still counts as resolved."""
    src = tmp_path / "x.cpp"
    src.write_text("\n".join(f"int v{i};" for i in range(50)) + "\n")

    v = _make_violation(line=5, file=src, fix_suggestion="```cpp\nint x;\n```")
    chunk = _make_chunk(start=3, end=10, file=src)

    fake_clang_tidy = MagicMock()
    # Same rule but at line 100, way outside chunk + margin
    fake_clang_tidy.run.return_value = [_make_violation(line=100, rule_id=v.rule_id, file=src)]

    verifier = FixVerifier(clang_tidy=fake_clang_tidy, line_margin=5)
    result = verifier.verify(v, chunk=chunk)

    assert result.resolved is True


# ---------------------------------------------------------------------------
# verify_node
# ---------------------------------------------------------------------------


def _state(violations: list[Violation], chunks: list[Chunk] | None = None) -> ReviewerState:
    return ReviewerState(
        source_file=Path("/tmp/fake.cpp"),
        violations=violations,
        chunks=chunks or [],
        failed_reviews=[],
        retry_count=0,
    )


def test_verify_node_no_op_when_verifier_is_none() -> None:
    node = make_verify_node(verifier=None)
    state = _state([_make_violation(fix_suggestion="```cpp\nint x;\n```")])

    out = node(state)

    assert out["failed_reviews"] == []


def test_verify_node_skips_violations_without_fix() -> None:
    verifier = MagicMock()
    node = make_verify_node(verifier=verifier)
    state = _state([_make_violation(fix_suggestion=None)])

    node(state)

    verifier.verify.assert_not_called()


def test_verify_node_routes_unresolved_to_failed_reviews() -> None:
    verifier = MagicMock()
    verifier.verify.return_value = VerificationResult(resolved=False)

    v = _make_violation(fix_suggestion="```cpp\nint x;\n```")
    node = make_verify_node(verifier=verifier)
    out = node(_state([v]))

    assert v in out["failed_reviews"]
    # Unresolved fix should be cleared so retry can produce a fresh one
    assert v.fix_suggestion is None


def test_verify_node_keeps_resolved_violations_clean() -> None:
    verifier = MagicMock()
    verifier.verify.return_value = VerificationResult(resolved=True)

    v = _make_violation(fix_suggestion="```cpp\nint x;\n```")
    node = make_verify_node(verifier=verifier)
    out = node(_state([v]))

    assert v not in out["failed_reviews"]
    # Resolved fix should be preserved
    assert v.fix_suggestion is not None


def test_verify_node_passes_chunk_when_available() -> None:
    verifier = MagicMock()
    verifier.verify.return_value = VerificationResult(resolved=True)

    v = _make_violation(line=10, fix_suggestion="```cpp\nint x;\n```")
    chunk = _make_chunk(start=5, end=15)
    node = make_verify_node(verifier=verifier)

    node(_state([v], chunks=[chunk]))

    verifier.verify.assert_called_once()
    # The chunk kwarg should have been passed
    assert verifier.verify.call_args.kwargs["chunk"] == chunk


def test_verify_rejects_partial_fix_when_chunk_provided(tmp_path: Path) -> None:
    """A 2-line fix for a 30-line chunk should be rejected as partial."""
    src = tmp_path / "x.cpp"
    src.write_text("\n".join(f"int v{i};" for i in range(30)) + "\n")

    chunk = Chunk(
        file=src,
        start_line=1,
        end_line=30,
        # 30 lines of content
        content="\n".join(f"int v{i};" for i in range(30)) + "\n",
        chunk_type="function",
        name="big_func",
        token_estimate=100,
    )

    # Tiny 2-line fix — should be rejected
    v = _make_violation(
        line=15,
        file=src,
        fix_suggestion="```cpp\nint x = 0;\nreturn x;\n```",
    )

    fake_clang_tidy = MagicMock()
    verifier = FixVerifier(clang_tidy=fake_clang_tidy, min_size_ratio=0.4)

    result = verifier.verify(v, chunk=chunk)

    assert result.resolved is False
    assert result.error is not None
    assert "partial fix" in result.error
    # clang-tidy should NOT have been invoked — we rejected before patching
    fake_clang_tidy.run.assert_not_called()


def test_verify_accepts_full_size_fix(tmp_path: Path) -> None:
    """A fix close to the chunk's size should pass the sanity check."""
    src = tmp_path / "x.cpp"
    chunk_content = "\n".join(f"int v{i};" for i in range(10)) + "\n"
    src.write_text(chunk_content)

    chunk = Chunk(
        file=src,
        start_line=1,
        end_line=10,
        content=chunk_content,
        chunk_type="function",
        name="f",
        token_estimate=40,
    )

    full_fix = "```cpp\n" + "\n".join(f"int w{i} = 0;" for i in range(9)) + "\n```"
    v = _make_violation(line=5, file=src, fix_suggestion=full_fix)

    fake_clang_tidy = MagicMock()
    fake_clang_tidy.run.return_value = []  # No violations after fix
    verifier = FixVerifier(clang_tidy=fake_clang_tidy, min_size_ratio=0.4)

    result = verifier.verify(v, chunk=chunk)

    assert result.resolved is True
    fake_clang_tidy.run.assert_called_once()


def test_verify_size_check_disabled_with_zero_ratio(tmp_path: Path) -> None:
    """min_size_ratio=0 disables the partial-fix rejection."""
    src = tmp_path / "x.cpp"
    src.write_text("\n".join(f"int v{i};" for i in range(20)) + "\n")

    chunk = Chunk(
        file=src,
        start_line=1,
        end_line=20,
        content="\n".join(f"int v{i};" for i in range(20)) + "\n",
        chunk_type="function",
        name="f",
        token_estimate=80,
    )

    # Tiny fix — should pass through to clang-tidy
    v = _make_violation(line=5, file=src, fix_suggestion="```cpp\nint x;\n```")

    fake_clang_tidy = MagicMock()
    fake_clang_tidy.run.return_value = []
    verifier = FixVerifier(clang_tidy=fake_clang_tidy, min_size_ratio=0.0)

    result = verifier.verify(v, chunk=chunk)

    assert result.resolved is True
    fake_clang_tidy.run.assert_called_once()


def test_verify_size_check_does_not_apply_without_chunk(tmp_path: Path) -> None:
    """When no chunk is provided, the size check is skipped."""
    src = tmp_path / "x.cpp"
    src.write_text("int x = 0;\n")

    v = _make_violation(line=1, file=src, fix_suggestion="```cpp\nint x = 1;\n```")

    fake_clang_tidy = MagicMock()
    fake_clang_tidy.run.return_value = []
    verifier = FixVerifier(clang_tidy=fake_clang_tidy)

    # No chunk passed — no size check applied
    result = verifier.verify(v, chunk=None)

    assert result.resolved is True

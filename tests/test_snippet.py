import typing
from pathlib import Path

from safecpp_reviewer.analyzer.models import Violation
from safecpp_reviewer.analyzer.snippet import extract_snippet


def _make_violation(file: Path, line: int) -> Violation:
    return Violation(
        file=file,
        line=line,
        rule_id="test:rule",
        severity="warning",
        message="x",
        tool="clang-tidy",
    )


def test_extract_snippet_marks_target_line(tmp_path: Path) -> None:
    """Test that the target line is marked with the marker symbol."""
    src: typing.Final = tmp_path / "f.cpp"
    print(src)
    src.write_text("line1\nline2\nline3\nline4\nline5\n")

    snippet: typing.Final = extract_snippet(_make_violation(src, 3), context_lines=1)

    assert snippet is not None
    assert ">>>" in snippet
    assert "line3" in snippet
    # only the target line gets the marker
    assert snippet.count(">>>") == 1


def test_extract_snippet_clamps_at_file_start(tmp_path: Path) -> None:
    """Ensure snippet extraction clamps context at the start of the file."""
    src: typing.Final = tmp_path / "f.cpp"
    src.write_text("a\nb\nc\n")

    snippet: typing.Final = extract_snippet(_make_violation(src, 1), context_lines=5)

    assert snippet is not None
    assert "a" in snippet
    # no negative line numbers in output
    assert "-1" not in snippet and " 0 " not in snippet


def test_extract_snippet_clamps_at_file_end(tmp_path: Path) -> None:
    """Ensure snippet extraction stops at the end of the file."""
    src: typing.Final = tmp_path / "f.cpp"
    src.write_text("a\nb\nc\n")

    snippet: typing.Final = extract_snippet(_make_violation(src, 3), context_lines=5)

    assert snippet is not None
    assert "c" in snippet


def test_extract_snippet_returns_none_on_missing_file() -> None:
    """Test that extract_snippet returns None when the file does not exist."""
    v: typing.Final = _make_violation(Path("/nonexistent/file.cpp"), 1)
    assert extract_snippet(v) is None


def test_extract_snippet_respects_context_size(tmp_path: Path) -> None:
    """Test that extract_snippet respects the context size."""
    src: typing.Final = tmp_path / "f.cpp"
    src.write_text("\n".join(f"line{i}" for i in range(1, 11)) + "\n")

    snippet: typing.Final = extract_snippet(_make_violation(src, 5), context_lines=2)

    assert snippet is not None
    # 2 above + target + 2 below = 5 lines
    assert len(snippet.splitlines()) == 5

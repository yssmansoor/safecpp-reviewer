"""Tests for the GitHub Action support code."""

from __future__ import annotations

import json
from pathlib import Path

from safecpp_reviewer.analyzer.changed_lines import (
    filter_to_changed_lines,
    load_changed_lines,
)
from safecpp_reviewer.analyzer.models import Violation
from safecpp_reviewer.report_github import render_github


def _v(
    file: str = "src/foo.cpp",
    line: int = 10,
    rule_id: str = "clang-tidy:test-rule",
    fix_suggestion: str | None = None,
) -> Violation:
    return Violation(
        file=Path(file),
        line=line,
        column=1,
        rule_id=rule_id,
        severity="warning",
        message="test message",
        tool="clang-tidy",
        fix_suggestion=fix_suggestion,
    )


# ---------------------------------------------------------------------------
# changed_lines
# ---------------------------------------------------------------------------


def test_load_changed_lines_normalises_separators(tmp_path: Path) -> None:
    p = tmp_path / "changed.json"
    p.write_text(json.dumps({"src\\foo.cpp": [1, 2, 3]}))

    out = load_changed_lines(p)

    assert "src/foo.cpp" in out
    assert out["src/foo.cpp"] == {1, 2, 3}


def test_filter_keeps_violation_on_changed_line() -> None:
    violations = [_v(line=10), _v(line=20)]
    changed = {"src/foo.cpp": {10, 11, 12}}

    out = filter_to_changed_lines(violations, changed)

    assert len(out) == 1
    assert out[0].line == 10


def test_filter_drops_violation_in_unchanged_file() -> None:
    violations = [_v(file="src/foo.cpp", line=10)]
    changed = {"src/bar.cpp": {10}}

    out = filter_to_changed_lines(violations, changed)
    assert out == []


def test_filter_handles_absolute_violation_path() -> None:
    """Violation has /repo/src/foo.cpp but diff has src/foo.cpp."""
    violations = [_v(file="/repo/src/foo.cpp", line=10)]
    changed = {"src/foo.cpp": {10}}

    out = filter_to_changed_lines(violations, changed)
    assert len(out) == 1


def test_filter_passes_through_when_changed_is_empty() -> None:
    violations = [_v(line=10), _v(line=20)]
    out = filter_to_changed_lines(violations, {})
    assert out == violations


# ---------------------------------------------------------------------------
# render_github
# ---------------------------------------------------------------------------


def test_render_github_basic_shape() -> None:
    violations = [_v(file="src/foo.cpp", line=10)]
    text = render_github(violations)
    payload = json.loads(text)

    assert "comments" in payload
    assert len(payload["comments"]) == 1

    c = payload["comments"][0]
    assert c["path"] == "src/foo.cpp"
    assert c["line"] == 10
    assert c["side"] == "RIGHT"
    assert "test message" in c["body"]
    assert "clang-tidy" in c["body"]


def test_render_github_includes_suggestion_block_for_fix() -> None:
    fix = "The cast is redundant since `a + b` is already int.\n\n```cpp\nint result = a + b;\n```"
    violations = [_v(fix_suggestion=fix)]
    payload = json.loads(render_github(violations))

    body = payload["comments"][0]["body"]
    assert "```suggestion" in body
    assert "int result = a + b;" in body


def test_render_github_writes_file_when_path_given(tmp_path: Path) -> None:
    out_path = tmp_path / "out" / "comments.json"
    render_github([_v()], output_path=out_path)

    assert out_path.exists()
    payload = json.loads(out_path.read_text())
    assert len(payload["comments"]) == 1


def test_render_github_uses_repo_root_for_relative_paths(tmp_path: Path) -> None:
    src = tmp_path / "src" / "foo.cpp"
    src.parent.mkdir()
    src.write_text("// stub\n")

    violations = [_v(file=str(src.resolve()), line=1)]
    payload = json.loads(render_github(violations, repo_root=tmp_path))

    assert payload["comments"][0]["path"] == "src/foo.cpp"


def test_render_github_skips_violations_outside_repo(tmp_path: Path) -> None:
    """If a violation's file isn't under repo_root, it gets skipped."""
    other_root = tmp_path / "other"
    other_root.mkdir()
    src = other_root / "outside.cpp"
    src.write_text("// stub\n")

    violations = [_v(file=str(src.resolve()), line=1)]
    payload = json.loads(render_github(violations, repo_root=tmp_path / "repo"))

    assert payload["comments"] == []

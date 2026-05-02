"""Unit tests for the static analysis wrappers.

These tests never invoke real clang-tidy or cppcheck binaries — they feed
pre-baked stdout/stderr strings directly into the parsers.  Integration tests
that need live tools are gated behind RUN_INTEGRATION_TESTS=1.
"""

# pylint: disable=protected-access

import os
import typing
from pathlib import Path

import pytest

from safecpp_reviewer.analyzer.clang_tidy import ClangTidyRunner
from safecpp_reviewer.analyzer.cppcheck import CppcheckRunner
from safecpp_reviewer.analyzer.models import Violation

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_CPP: typing.Final = Path("/tmp/fake_source.cpp")

CLANG_TIDY_OUTPUT: typing.Final = """\
/tmp/fake_source.cpp:10:5: warning: do not use C-style cast [cppcoreguidelines-pro-type-cstyle-cast]
    int x = (int)y;
    ^
/tmp/fake_source.cpp:20:1: error: use of undeclared identifier 'foo' [clang-diagnostic-error]
foo();
^
"""

CPPCHECK_XML: typing.Final = """\
<?xml version="1.0" encoding="UTF-8"?>
<results version="2">
  <cppcheck version="2.13"/>
  <errors>
    <error id="nullPointer" severity="error" msg="Null pointer dereference" verbose="Possible null pointer dereference: ptr">
      <location file="/tmp/fake_source.cpp" line="15" column="3"/>
    </error>
    <error id="unusedVariable" severity="style" msg="Unused variable: x" verbose="The variable 'x' is assigned a value that is never used.">
      <location file="/tmp/fake_source.cpp" line="8" column="9"/>
    </error>
    <error id="missingReturn" severity="error" msg="Missing return statement" verbose="Missing return statement in function returning non-void.">
      <location file="/usr/include/stdlib.h" line="1" column="1"/>
    </error>
  </errors>
</results>
"""


# ---------------------------------------------------------------------------
# Violation model
# ---------------------------------------------------------------------------


def test_violation_short_includes_key_fields() -> None:
    """Test that short includes key fields."""
    v: typing.Final = Violation(
        file=Path("/tmp/foo.cpp"),
        line=42,
        column=7,
        rule_id="clang-tidy:cppcoreguidelines-pro-type-cstyle-cast",
        severity="warning",
        message="do not use C-style cast",
        tool="clang-tidy",
    )
    short: typing.Final = v.short()
    assert "foo.cpp" in short
    assert "42" in short
    assert "cppcoreguidelines" in short


def test_violation_column_optional() -> None:
    """Test that column is optional."""
    v: typing.Final = Violation(
        file=Path("/tmp/foo.cpp"),
        line=1,
        rule_id="cppcheck:nullPointer",
        severity="error",
        message="null pointer",
        tool="cppcheck",
    )
    assert v.column is None
    assert ":None" not in v.short()


# ---------------------------------------------------------------------------
# clang-tidy parser
# ---------------------------------------------------------------------------


def test_clang_tidy_parses_warning() -> None:
    """Test that clang-tidy parses warnings correctly."""
    runner: typing.Final = ClangTidyRunner()
    violations: typing.Final = runner._parse(CLANG_TIDY_OUTPUT, FAKE_CPP)

    assert len(violations) == 2

    warning: typing.Final = violations[0]
    assert warning.line == 9
    assert warning.column == 5
    assert warning.severity == "warning"
    assert warning.rule_id == "clang-tidy:cppcoreguidelines-pro-type-cstyle-cast"
    assert warning.tool == "clang-tidy"
    assert warning.category == "cppcoreguidelines"


def test_clang_tidy_parses_error() -> None:
    """Test that clang-tidy parses errors correctly."""
    runner: typing.Final = ClangTidyRunner()
    violations: typing.Final = runner._parse(CLANG_TIDY_OUTPUT, FAKE_CPP)

    error: typing.Final = violations[1]
    assert error.line == 19
    assert error.severity == "error"


def test_clang_tidy_ignores_other_files() -> None:
    """Test that clang-tidy ignores violations from other files."""
    output: typing.Final = "/other/file.cpp:5:1: warning: something [some-check]\n"
    runner: typing.Final = ClangTidyRunner()
    violations: typing.Final = runner._parse(output, FAKE_CPP)
    assert not violations


def test_clang_tidy_raises_on_missing_file() -> None:
    """Test that clang-tidy raises on missing file."""
    runner: typing.Final = ClangTidyRunner()
    with pytest.raises(FileNotFoundError):
        runner.run(Path("/nonexistent/file.cpp"))


# ---------------------------------------------------------------------------
# cppcheck parser
# ---------------------------------------------------------------------------


def test_cppcheck_parses_error() -> None:
    """Test that cppcheck parses errors correctly."""
    runner: typing.Final = CppcheckRunner()
    violations: typing.Final = runner._parse_xml(CPPCHECK_XML, FAKE_CPP)

    # Should parse 2 — the stdlib.h one is filtered out
    assert len(violations) == 2


def test_cppcheck_null_pointer_violation() -> None:
    """Test that cppcheck parses null pointer violations correctly."""
    runner: typing.Final = CppcheckRunner()
    violations: typing.Final = runner._parse_xml(CPPCHECK_XML, FAKE_CPP)

    null_ptr: typing.Final = next(v for v in violations if "nullPointer" in v.rule_id)
    assert null_ptr.line == 14
    assert null_ptr.severity == "error"
    assert null_ptr.tool == "cppcheck"
    assert null_ptr.category == "cppcheck"


def test_cppcheck_style_violation() -> None:
    """Test that cppcheck parses style violations correctly."""
    runner: typing.Final = CppcheckRunner()
    violations: typing.Final = runner._parse_xml(CPPCHECK_XML, FAKE_CPP)

    unused: typing.Final = next(v for v in violations if "unusedVariable" in v.rule_id)
    assert unused.severity == "style"
    assert unused.line == 7


def test_cppcheck_filters_system_headers() -> None:
    """Test that cppcheck filters out system headers."""
    runner: typing.Final = CppcheckRunner()
    violations: typing.Final = runner._parse_xml(CPPCHECK_XML, FAKE_CPP)
    files: typing.Final = [str(v.file) for v in violations]
    assert not any("stdlib.h" in f for f in files)


def test_cppcheck_invalid_xml_returns_empty() -> None:
    """Test that cppcheck returns empty list for invalid XML."""
    runner: typing.Final = CppcheckRunner()
    violations: typing.Final = runner._parse_xml("this is not xml", FAKE_CPP)
    assert not violations


def test_cppcheck_raises_on_missing_file() -> None:
    """Test that cppcheck raises on missing file."""
    runner: typing.Final = CppcheckRunner()
    with pytest.raises(FileNotFoundError):
        runner.run(Path("/nonexistent/file.cpp"))


# ---------------------------------------------------------------------------
# Integration tests (need live tools)
# ---------------------------------------------------------------------------

needs_tools = pytest.mark.skipif(
    not os.getenv("RUN_INTEGRATION_TESTS"),
    reason="Set RUN_INTEGRATION_TESTS=1 and ensure clang-tidy + cppcheck are installed",
)

SAMPLE_CPP: typing.Final = """\
#include <cstdlib>

int add(int a, int b) {
    int* ptr = NULL;       // cppcheck: nullPointer risk / modernize-use-nullptr
    int result = (int)(a + b);  // cppcoreguidelines: c-style cast
    return result;
}
"""


@needs_tools
def test_integration_clang_tidy_finds_violations(tmp_path: Path) -> None:
    """Test that clang-tidy finds violations in integration."""
    src: typing.Final = tmp_path / "sample.cpp"
    src.write_text(SAMPLE_CPP)

    runner: typing.Final = ClangTidyRunner(checks="cppcoreguidelines-*,modernize-*")
    violations: typing.Final = runner.run(src)

    assert len(violations) > 0
    assert all(v.tool == "clang-tidy" for v in violations)


@needs_tools
def test_integration_cppcheck_finds_violations(tmp_path: Path) -> None:
    src: typing.Final = tmp_path / "sample.cpp"
    src.write_text(SAMPLE_CPP)

    runner: typing.Final = CppcheckRunner()
    violations: typing.Final = runner.run(src)

    assert len(violations) > 0
    assert all(v.tool == "cppcheck" for v in violations)

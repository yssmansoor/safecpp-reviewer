"""Tests for the meta-diagnostic filter."""

from pathlib import Path

from safecpp_reviewer.analyzer.filter import (
    filter_real_violations,
    is_real_violation,
)
from safecpp_reviewer.analyzer.models import Violation


def _v(rule_id: str) -> Violation:
    return Violation(
        file=Path("/tmp/x.cpp"),
        line=1,
        column=1,
        rule_id=rule_id,
        severity="warning",
        message="m",
        tool="clang-tidy",
    )


def test_real_rule_passes() -> None:
    assert is_real_violation(_v("clang-tidy:cppcoreguidelines-pro-type-cstyle-cast"))


def test_clang_diagnostic_error_filtered() -> None:
    assert not is_real_violation(_v("clang-tidy:clang-diagnostic-error"))


def test_clang_diagnostic_warning_filtered() -> None:
    assert not is_real_violation(_v("clang-tidy:clang-diagnostic-warning"))


def test_clang_tidy_nolint_meta_filtered() -> None:
    assert not is_real_violation(_v("clang-tidy:clang-tidy-nolint"))


def test_unknown_clang_diagnostic_prefix_filtered() -> None:
    assert not is_real_violation(_v("clang-tidy:clang-diagnostic-unknown-pragma"))


def test_filter_real_violations_strips_only_meta() -> None:
    items = [
        _v("clang-tidy:cppcoreguidelines-avoid-magic-numbers"),
        _v("clang-tidy:clang-diagnostic-error"),
        _v("clang-tidy:modernize-use-nullptr"),
        _v("clang-tidy:clang-tidy-nolint"),
    ]
    out = filter_real_violations(items)
    assert len(out) == 2
    assert {v.rule_id for v in out} == {
        "clang-tidy:cppcoreguidelines-avoid-magic-numbers",
        "clang-tidy:modernize-use-nullptr",
    }


def test_cppcheck_violations_pass_through() -> None:
    assert is_real_violation(_v("cppcheck:nullPointer"))
    assert is_real_violation(_v("cppcheck:unusedVariable"))

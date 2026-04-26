from pathlib import Path

from safecpp_reviewer.analyzer.clang_tidy import run_clang_tidy
from safecpp_reviewer.analyzer.cppcheck import run_cppcheck


def test_cppcheck_detects_issue():

    file = Path("tests/data/bad_code.cpp")

    violations = run_cppcheck(file)

    assert len(violations) > 0


def test_clang_tidy_detects_issue():

    file = Path("tests/data/bad_code.cpp")

    violations = run_clang_tidy(file)

    assert len(violations) > 0

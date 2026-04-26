import os
from pathlib import Path

import pytest

from safecpp_reviewer.analyzer.clang_tidy import run_clang_tidy
from safecpp_reviewer.analyzer.cppcheck import run_cppcheck

# Skip marker: tests below only run when RUN_INTEGRATION_TESTS is set.
needs_static_analyzer_tools = pytest.mark.skipif(
    not os.getenv("RUN_INTEGRATION_TESTS"),
    reason="Set RUN_INTEGRATION_TESTS=1 and run `make serve` first",
)


@needs_static_analyzer_tools
def test_cppcheck_detects_issue():

    file = Path("tests/data/bad_code.cpp")

    violations = run_cppcheck(file)

    assert len(violations) > 0


@needs_static_analyzer_tools
def test_clang_tidy_detects_issue():

    file = Path("tests/data/bad_code.cpp")

    violations = run_clang_tidy(file)

    assert len(violations) > 0

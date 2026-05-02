import typing
from pathlib import Path

from safecpp_reviewer.analyzer.models import Violation  # type: ignore


def test_violation_serialization() -> None:
    """Test that Violation objects are serialized correctly."""
    v: typing.Final = Violation(
        tool="clang-tidy",
        file=Path("main.cpp"),
        line=10,
        column=5,
        severity="warning",
        rule_id="cppcoreguidelines-owning-memory",
        message="Avoid raw pointers",
    )

    data: typing.Final = v.model_dump()

    assert data["tool"] == "clang-tidy"
    assert data["line"] == 10
    assert data["column"] == 5
    assert data["severity"] == "warning"
    assert data["rule_id"] == "cppcoreguidelines-owning-memory"
    assert data["message"] == "Avoid raw pointers"

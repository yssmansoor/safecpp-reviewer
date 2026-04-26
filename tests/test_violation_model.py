from safecpp_reviewer.analyzer.models import Violation


def test_violation_serialization():
    v = Violation(
        tool="clang-tidy",
        file_path="main.cpp",
        line=10,
        column=5,
        severity="warning",
        rule_id="cppcoreguidelines-owning-memory",
        message="Avoid raw pointers",
    )

    data = v.model_dump()

    assert data["tool"] == "clang-tidy"
    assert data["line"] == 10

"""Sanity tests to verify test infrastructure works."""


def test_sanity() -> None:
    """Trivial test - confirms pytest collects and runs tests."""
    assert 1 + 1 == 2


def test_imports() -> None:
    """Verify the package imports without errors."""
    import safecpp_reviewer  # noqa: F401

"""Tests for the knowledge base."""

import typing
from pathlib import Path

from safecpp_reviewer.knowledge import Rule, RuleStore


def test_rule_for_prompt_includes_rationale() -> None:
    rule: typing.Final = Rule(
        rule_id="clang-tidy:test-rule",
        short_name="Test rule",
        category="test",
        rationale="Because reasons.",
    )
    text: typing.Final = rule.for_prompt()
    assert "test-rule" in text
    assert "Because reasons." in text


def test_rule_for_prompt_includes_examples_when_present() -> None:
    rule: typing.Final = Rule(
        rule_id="x",
        short_name="x",
        category="x",
        rationale="x",
        bad_example="int* p = NULL;",
        good_example="int* p = nullptr;",
    )
    text: typing.Final = rule.for_prompt()
    assert "NULL" in text
    assert "nullptr" in text


def test_store_loads_default_data() -> None:
    store: typing.Final = RuleStore.from_default_data()
    assert len(store) > 0
    # We hand-authored these — they should be present
    assert "clang-tidy:cppcoreguidelines-pro-type-cstyle-cast" in store
    assert "clang-tidy:modernize-use-nullptr" in store


def test_store_get_returns_none_for_unknown_rule() -> None:
    store: typing.Final = RuleStore.from_default_data()
    assert store.get("clang-tidy:nonexistent-rule") is None


def test_store_from_directory_skips_invalid_files(tmp_path: Path) -> None:
    # Valid file
    (tmp_path / "good.yaml").write_text(
        "rules:\n"
        "  - rule_id: test:foo\n"
        "    short_name: Foo\n"
        "    category: test\n"
        "    rationale: Just a test.\n"
    )
    # Invalid file (not parseable)
    (tmp_path / "bad.yaml").write_text("this: is: not: valid: yaml: [")

    store: typing.Final = RuleStore.from_directory(tmp_path)
    assert "test:foo" in store


def test_store_handles_empty_yaml(tmp_path: Path) -> None:
    (tmp_path / "empty.yaml").write_text("")
    store: typing.Final = RuleStore.from_directory(tmp_path)
    assert len(store) == 0


def test_store_handles_single_rule_file(tmp_path: Path) -> None:
    """YAML with a single top-level rule object (not wrapped in 'rules')."""
    (tmp_path / "single.yaml").write_text(
        "rule_id: test:bar\nshort_name: Bar\ncategory: test\nrationale: Single rule.\n"
    )
    store: typing.Final = RuleStore.from_directory(tmp_path)
    assert "test:bar" in store


def test_store_missing_directory_returns_empty() -> None:
    store: typing.Final = RuleStore.from_directory(Path("/nonexistent/path"))
    assert len(store) == 0

"""Tests for the LlamaCppClient.

These tests are split into two categories:
    - Unit tests that don't need a running server (always run).
    - Integration tests that need a live llama.cpp server (skipped by default,
      enabled by setting RUN_INTEGRATION_TESTS=1).

To run integration tests:
    1. In one terminal: `make serve`
    2. In another:      `RUN_INTEGRATION_TESTS=1 pytest`
"""

import os
import typing

import pytest
from pydantic import ValidationError

from safecpp_reviewer.llm.client import (
    CompletionResult,
    LlamaCppClient,
    LlamaCppError,
)

# ----------------------------------------------------------------------------
# Pure unit tests (no network, always run)
# ----------------------------------------------------------------------------


def test_completion_result_validates_happy_path() -> None:
    """CompletionResult constructs with valid data."""
    result: typing.Final = CompletionResult(
        text="hello world",
        tokens_generated=2,
        tokens_per_second=42.5,
        prompt_tokens=10,
    )
    assert result.text == "hello world"
    assert result.tokens_generated == 2
    assert result.tokens_per_second == 42.5
    assert result.prompt_tokens == 10


def test_completion_result_rejects_invalid_types() -> None:
    """CompletionResult raises ValidationError on type mismatches."""
    with pytest.raises(ValidationError):
        CompletionResult(
            text="hello",
            tokens_generated="not an int",  # type: ignore[arg-type]
            tokens_per_second=42.5,
            prompt_tokens=10,
        )


def test_client_strips_trailing_slash_from_base_url() -> None:
    """Trailing slashes on base_url should not produce double slashes in paths."""
    client: typing.Final = LlamaCppClient(base_url="http://127.0.0.1:8080/")
    assert client.base_url == "http://127.0.0.1:8080"


def test_health_returns_false_on_unreachable_host() -> None:
    """health_check() should return False (not raise) when the server is down."""
    # Port 1 is reserved and nothing should be listening there.
    client: typing.Final = LlamaCppClient(base_url="http://127.0.0.1:1", timeout=1.0)
    assert client.health_check() is False


# ----------------------------------------------------------------------------
# Integration tests (need a live llama.cpp server)
# ----------------------------------------------------------------------------


# Skip marker: tests below only run when RUN_INTEGRATION_TESTS is set.
needs_live_server = pytest.mark.skipif(
    not os.getenv("RUN_INTEGRATION_TESTS"),
    reason="Set RUN_INTEGRATION_TESTS=1 and run `make serve` first",
)


@needs_live_server
def test_health_returns_true_when_server_is_running() -> None:
    """health_check() returns True against a real running server."""
    client: typing.Final = LlamaCppClient()
    assert client.health_check() is True


@needs_live_server
def test_complete_with_real_server() -> None:
    """complete() returns a sensible result against a real running server."""
    client: typing.Final = LlamaCppClient()

    result: typing.Final = client.complete(
        prompt="Reply with exactly the single word: ready",
        system="You are a precise assistant. Reply with exactly what is asked, no more.",
        temperature=0.0,
        max_tokens=10,
    )

    # We can't assert the exact text (the model is non-deterministic even at
    # temp 0 on different runs because of fp rounding), but we can assert
    # the response is sane.
    assert len(result.text) > 0
    assert result.tokens_generated > 0
    assert result.prompt_tokens > 0
    # Tokens-per-second might be 0 if timings aren't reported, but if they
    # are, they should be positive.
    assert result.tokens_per_second >= 0.0


@needs_live_server
def test_complete_raises_on_unreachable_server() -> None:
    """complete() raises LlamaCppError when the server can't be reached."""
    client: typing.Final = LlamaCppClient(base_url="http://127.0.0.1:1", timeout=2.0)
    with pytest.raises(LlamaCppError):
        client.complete(prompt="hello", max_tokens=10)

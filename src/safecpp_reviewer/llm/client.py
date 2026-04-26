"""
HTTP client for talking to a local llama.cpp inference server.

The llama.cpp server exposes an OpenAI-compatible API. This module wraps it
with a typed, ergonomic Python interface that the rest of the project will
use for all LLM interactions.

Design notes:
    - Synchronous (httpx.post) rather than async. The agent's plan->generate->
      validate->refine loop is naturally sequential, so async adds complexity
      without benefit. We can revisit if we ever batch requests.
    - All errors funnel through LlamaCppError. Callers should catch this
      single exception type rather than the variety of httpx/JSON errors
      that could occur underneath.
    - Returns a Pydantic model rather than a dict, so callers get autocomplete
      and type checking on the result fields.
"""

from typing import Any

import httpx
from pydantic import BaseModel


class LlamaCppError(Exception):
    """Raised when the llama.cpp server returns an error or is unreachable."""


class CompletionResult(BaseModel):
    """Result of a single text completion request.

    Attributes:
        text: The generated text from the model.
        tokens_generated: Number of tokens the model produced.
        tokens_per_second: Generation throughput as reported by llama.cpp.
            Defaults to 0.0 if the server didn't include timing info.
        prompt_tokens: Number of tokens in the input prompt (after templating).
    """

    text: str
    tokens_generated: int
    tokens_per_second: float
    prompt_tokens: int


class LlamaCppClient:
    """Synchronous client for a local llama.cpp HTTP server.

    Example:
        >>> client = LlamaCppClient()
        >>> if client.health():
        ...     result = client.complete("Write a C++ comment.")
        ...     print(result.text)
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080",
        timeout: float = 120.0,
    ) -> None:
        """Initialize the client.

        Args:
            base_url: The llama.cpp server's base URL. No trailing slash.
            timeout: Request timeout in seconds. Generation can take a while
                on long prompts; 120s is a generous default.
        """
        # Strip trailing slash so we can safely concat paths like
        # f"{self.base_url}/v1/chat/completions" without doubling the slash.
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> bool:
        """Check whether the llama.cpp server is responsive.

        Returns:
            True if GET /health returns 200, False on any error or non-200.

        This method never raises. It's designed to be safe to call in a
        retry loop or startup check without try/except boilerplate.
        """
        try:
            response = httpx.get(
                f"{self.base_url}/health",
                timeout=self.timeout,
            )
            return response.status_code == 200
        except httpx.HTTPError:
            # Covers connect errors, timeouts, DNS failures, and read errors.
            # We deliberately swallow these because health() is meant to be
            # a quiet probe, not a noisy alarm.
            return False

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 512,
        stop: list[str] | None = None,
    ) -> CompletionResult:
        """Generate a completion from the model.

        Args:
            prompt: The user message.
            system: Optional system message that sets the model's behavior.
            temperature: Sampling temperature. Lower (0.0-0.3) = more
                deterministic, better for code. Higher (0.7-1.0) = more
                creative.
            max_tokens: Cap on tokens the model can produce.
            stop: Optional list of stop sequences. Generation halts when any
                appears in the output.

        Returns:
            A CompletionResult with the text and timing info.

        Raises:
            LlamaCppError: On any failure (network, server error, malformed
                response). The original exception is chained via __cause__.
        """
        # Build messages in OpenAI chat format. System message goes first
        # if provided; user message always comes last.
        messages: list[dict[str, str]] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        # Build the request body. Use Any for the value type because the body
        # mixes types (lists, strings, floats, ints).
        body: dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if stop is not None:
            body["stop"] = stop

        # Issue the request and convert any failure to LlamaCppError.
        # We use `from e` to preserve the original exception in the traceback,
        # which is invaluable when debugging.
        try:
            response = httpx.post(
                f"{self.base_url}/v1/chat/completions",
                json=body,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as e:
            raise LlamaCppError(f"HTTP request failed: {e}") from e
        except ValueError as e:
            # response.json() raises ValueError on malformed JSON.
            raise LlamaCppError(f"Server returned invalid JSON: {e}") from e

        # Extract fields. KeyError or IndexError here means the response
        # didn't match the OpenAI schema, which we treat as a server error.
        try:
            text = data["choices"][0]["message"]["content"]
            usage = data["usage"]
            prompt_tokens = usage["prompt_tokens"]
            completion_tokens = usage["completion_tokens"]
        except (KeyError, IndexError) as e:
            raise LlamaCppError(f"Unexpected response schema: missing key {e}") from e

        # Timings are llama.cpp-specific and may not exist on all server
        # versions. Default to 0.0 so callers don't have to handle missing
        # data; if they care about throughput, they can check for > 0.
        timings = data.get("timings", {})
        tokens_per_second = float(timings.get("predicted_per_second", 0.0))

        return CompletionResult(
            text=text,
            tokens_generated=completion_tokens,
            tokens_per_second=tokens_per_second,
            prompt_tokens=prompt_tokens,
        )

import logging
import random
import time
import typing
from collections.abc import Callable, Generator, Iterable
from typing import Any

from openai import APIError, APITimeoutError, OpenAI, RateLimitError
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel

logger = logging.getLogger(__name__)


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
    """Client for text completion using Llama.cpp or OpenAI API."""

    def __init__(
        self,
        provider: str = "local",  # "local" | "openai"
        model: str = "qwen2.5-coder-7b",
        api_key: str | None = None,
        base_url: str = "http://127.0.0.1:8080",
        timeout: float = 60.0,
        max_retries: int = 3,
        backoff_base: float = 1.5,
    ) -> None:
        self.provider = provider
        self.model = model
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.base_url = base_url.rstrip("/")

        # -------------------------
        # Provider config
        # -------------------------
        if provider == "local":
            self.client = OpenAI(
                api_key=api_key or "local",
                base_url=base_url or "http://127.0.0.1:8080",
                timeout=timeout,
            )
        elif provider == "openai":
            self.client = OpenAI(
                api_key=api_key,
                timeout=timeout,
            )
        else:
            logger.exception(f"Unsupported provider: {provider}")
            raise LlamaCppError(f"Unsupported provider: {provider}")

    # -------------------------
    # Retry wrapper
    # -------------------------
    def _with_retries(self, func: Callable[[], Any]) -> Any:
        err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return func()

            except (RateLimitError, APITimeoutError) as e:
                err = e
                if attempt == self.max_retries:
                    logger.error("Max retries exhausted", exc_info=True)

                sleep_time = (self.backoff_base**attempt) + random.uniform(0, 0.5)
                logger.warning(
                    f"[Retry {attempt + 1}/{self.max_retries}] {e} → sleep {sleep_time:.2f}s"
                )
                time.sleep(sleep_time)

            except APIError as e:
                logger.exception("API error")
                raise LlamaCppError(f"API request failed: {e}") from e

            except Exception as e:
                logger.exception("Unexpected error")
                raise LlamaCppError("Unexpected error") from e

        raise LlamaCppError("Max retries exhausted") from err

    # -------------------------
    # Completion
    # -------------------------
    def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> Any:
        messages: typing.Final[list[ChatCompletionMessageParam]] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        def _call() -> CompletionResult:
            response: typing.Final = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )

            choice: typing.Final = response.choices[0].message
            text: typing.Final[str] = choice.content or ""

            usage: typing.Final = getattr(response, "usage", None)
            prompt_tokens: typing.Final[int] = getattr(usage, "prompt_tokens", 0) if usage else 0
            completion_tokens: typing.Final[int] = (
                getattr(usage, "completion_tokens", len(text.split()))
                if usage
                else len(text.split())
            )
            timings: typing.Final[dict[str, Any]] = getattr(response, "timings", {})
            tokens_per_second: typing.Final = float(timings.get("predicted_per_second", 0.0))

            return CompletionResult(
                text=text,
                tokens_generated=completion_tokens,
                tokens_per_second=tokens_per_second,
                prompt_tokens=prompt_tokens,
            )

        return self._with_retries(_call)

    # -------------------------
    # Streaming
    # -------------------------
    def stream_chat(
        self,
        messages: Iterable[ChatCompletionMessageParam],
        **kwargs: Any,
    ) -> Generator[str, None, None]:
        def _call() -> Any:
            return self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True,
                **kwargs,
            )

        stream: typing.Final = self._with_retries(_call)

        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content

    # -------------------------
    # Health check
    # -------------------------
    def health_check(self) -> bool:
        try:
            self.complete("ping", max_tokens=1)
            return True
        except Exception:
            return False

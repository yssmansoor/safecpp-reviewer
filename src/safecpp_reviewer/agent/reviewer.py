import logging
import re
import typing

from safecpp_reviewer.chunker.models import Chunk
from safecpp_reviewer.knowledge import RuleStore

from ..agent.prompts import (
    CHUNK_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_chunk_user_prompt,
    build_user_prompt,
)
from ..analyzer.models import ChunkReviewResponse, ReviewResponse, Violation
from ..llm.client import LlamaCppClient

logger = logging.getLogger(__name__)


class ViolationReviewer:
    """Review C++ violations and provide explanations and fix suggestions."""

    # _FENCE_RE = re.compile(
    #     r'"explanation"\s*:\s*"(?P<exp>(?:[^"\\]|\\.)*)"'
    #     r".*?"
    #     r'"fixed_code"\s*:\s*"(?P<code>.*?)"\s*\}',
    #     re.DOTALL,
    # )

    _FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)

    def __init__(self, client: LlamaCppClient, rule_store: RuleStore | None = None) -> None:
        self.client = client
        self.rule_store = rule_store or RuleStore.from_default_data()

    def _parse_review_response(self, text: str) -> ReviewResponse | None:
        """Parse LLM output into ReviewResponse, tolerant of malformed JSON."""
        # Try strict JSON first
        try:
            return ReviewResponse.model_validate_json(text)
        except Exception:
            pass

        # Try after escaping raw newlines inside string values
        try:
            # Replace literal newlines inside quoted strings with \n
            fixed: typing.Final = re.sub(
                r'"((?:[^"\\]|\\.)*)"',
                lambda m: '"' + m.group(1).replace("\n", "\\n").replace("\r", "") + '"',
                text,
                flags=re.DOTALL,
            )
            return ReviewResponse.model_validate_json(fixed)
        except Exception:
            pass

        # Last resort: regex extraction
        m: typing.Final = ViolationReviewer._FENCE_RE.search(text)
        if m:
            return ReviewResponse(
                explanation=m.group("exp").strip(),
                fixed_code=m.group("code").replace('\\"', '"').strip(),
            )

        return None

    def _parse_chunk_review_response(self, text: str) -> ChunkReviewResponse | None:
        """Parse LLM output into ChunkReviewResponse, tolerant of raw newlines."""
        try:
            return ChunkReviewResponse.model_validate_json(text)
        except Exception:
            pass

        try:
            fixed: typing.Final = re.sub(
                r'"((?:[^"\\]|\\.)*)"',
                lambda m: '"' + m.group(1).replace("\n", "\\n").replace("\r", "") + '"',
                text,
                flags=re.DOTALL,
            )
            return ChunkReviewResponse.model_validate_json(fixed)
        except Exception:
            return None

    # def _strip_fences(self, text: str) -> str:
    #     """Remove markdown code fences if present."""
    #     text = text.strip()
    #     m: typing.Final = self._FENCE_RE.match(text)
    #     return m.group(1).strip() if m else text

    def _strip_fences(self, text: str) -> str:
        text = text.strip()
        m: typing.Final = self._FENCE_RE.match(text)
        return m.group(1).strip() if m else text

    def _strip_code_fence(self, code: str) -> str:
        """Remove markdown fences from inside fixed_code if the model added them."""
        code = code.strip()
        # Remove leading ```cpp / ```c++ / ```
        code = re.sub(r"^```(?:cpp|c\+\+)?\s*\n?", "", code)
        # Remove trailing ```
        code = re.sub(r"\n?```\s*$", "", code)
        # Replace literal \n strings (LLM artifact) with real newlines
        code = code.replace("\\n", "\n")
        return code.strip()

    def review(self, violation: Violation) -> Violation:
        """Ask the LLM to explain the violation and suggest a fix.

        Returns the same violation with fix_suggestion populated.
        """
        try:
            prompt: typing.Final = build_user_prompt(violation, self.rule_store)
            result: typing.Final = self.client.complete(
                prompt, system=SYSTEM_PROMPT, temperature=0.1, max_tokens=2048
            )
            cleaned: typing.Final = self._strip_fences(result.text)
            response: typing.Final = self._parse_review_response(cleaned)

            if response is None:
                logger.warning(
                    "Could not parse LLM output for %s. Raw: %r",
                    violation.short(),
                    result.text[:200],
                )
                return violation

            fixed_code: typing.Final = self._strip_code_fence(response.fixed_code)
            violation.fix_suggestion = f"{response.explanation}\n\n```cpp\n{fixed_code}\n```"
        except Exception as e:
            logger.warning("LLM review failed: %s", e)

        return violation

    def review_chunk(self, chunk: Chunk, violations: list[Violation]) -> list[Violation]:
        """Review all violations in a single chunk in one LLM call.

        Returns the same violations with fix_suggestion populated.
        """
        if not violations:
            return violations

        try:
            prompt: typing.Final = build_chunk_user_prompt(chunk, violations, self.rule_store)
            result: typing.Final = self.client.complete(
                prompt, system=CHUNK_SYSTEM_PROMPT, temperature=0.1, max_tokens=2048
            )
            cleaned: typing.Final = self._strip_fences(result.text)
            response: typing.Final = self._parse_chunk_review_response(cleaned)

            if response is None:
                logger.warning(
                    "Could not parse LLM chunk output for %s:%s-%s. Raw: %r",
                    chunk.file,
                    chunk.start_line,
                    chunk.end_line,
                    result.text[:200],
                )
                return violations

            zero_based_indexes_are_valid: typing.Final = all(
                0 <= review.violation_index < len(violations) for review in response.reviews
            )
            one_based_indexes_are_valid: typing.Final = all(
                1 <= review.violation_index <= len(violations) for review in response.reviews
            )
            use_one_based_indexes: typing.Final = (
                not zero_based_indexes_are_valid and one_based_indexes_are_valid
            )
            assigned_indexes: typing.Final[set[int]] = set()
            for response_position, review in enumerate(response.reviews):
                violation_index = (
                    review.violation_index - 1 if use_one_based_indexes else review.violation_index
                )
                if not 0 <= violation_index < len(violations):
                    if response_position < len(violations):
                        violation_index = response_position
                    elif len(violations) == 1 and 0 not in assigned_indexes:
                        violation_index = 0
                    elif len(violations) == 1 and 0 in assigned_indexes:
                        logger.debug(
                            "Ignoring duplicate extra chunk review with violation_index=%s for %s:%s-%s",
                            review.violation_index,
                            chunk.file,
                            chunk.start_line,
                            chunk.end_line,
                        )
                        continue
                    else:
                        logger.warning(
                            "Ignoring chunk review with invalid violation_index=%s for %s:%s-%s",
                            review.violation_index,
                            chunk.file,
                            chunk.start_line,
                            chunk.end_line,
                        )
                        continue

                if violation_index in assigned_indexes:
                    logger.debug(
                        "Ignoring duplicate chunk review for violation_index=%s in %s:%s-%s",
                        review.violation_index,
                        chunk.file,
                        chunk.start_line,
                        chunk.end_line,
                    )
                    continue

                if review.violation_index != violation_index:
                    logger.debug(
                        "Mapped chunk review violation_index=%s to violation_index=%s for %s:%s-%s",
                        review.violation_index,
                        violation_index,
                        chunk.file,
                        chunk.start_line,
                        chunk.end_line,
                    )

                fixed_code = self._strip_code_fence(review.fixed_code)
                violations[
                    violation_index
                ].fix_suggestion = f"{review.explanation}\n\n```cpp\n{fixed_code}\n```"
                assigned_indexes.add(violation_index)
        except Exception as e:
            logger.warning("LLM chunk review failed: %s", e)

        return violations

import logging
import re

from ..agent.prompts import SYSTEM_PROMPT, build_user_prompt
from ..analyzer.models import ReviewResponse, Violation
from ..llm.client import LlamaCppClient

logger = logging.getLogger(__name__)


class ViolationReviewer:
    """Review C++ violations and provide explanations and fix suggestions."""

    _FENCE_RE = re.compile(
        r'"explanation"\s*:\s*"(?P<exp>(?:[^"\\]|\\.)*)"'
        r".*?"
        r'"fixed_code"\s*:\s*"(?P<code>.*?)"\s*\}',
        re.DOTALL,
    )

    def __init__(self, client: LlamaCppClient) -> None:
        self.client = client

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
            fixed = re.sub(
                r'"((?:[^"\\]|\\.)*)"',
                lambda m: '"' + m.group(1).replace("\n", "\\n").replace("\r", "") + '"',
                text,
                flags=re.DOTALL,
            )
            return ReviewResponse.model_validate_json(fixed)
        except Exception:
            pass

        # Last resort: regex extraction
        m = ViolationReviewer._FENCE_RE.search(text)
        if m:
            return ReviewResponse(
                explanation=m.group("exp").strip(),
                fixed_code=m.group("code").replace('\\"', '"').strip(),
            )

        return None

    def _strip_fences(self, text: str) -> str:
        """Remove markdown code fences if present."""
        text = text.strip()
        m = self._FENCE_RE.match(text)
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
        # prompt = build_user_prompt(violation)
        # result = self.client.complete(prompt, system=SYSTEM_PROMPT, temperature=0.1, max_tokens=512)
        # cleaned = self._strip_fences(result.text)
        # response = ReviewResponse.model_validate_json(cleaned)
        # violation.fix_suggestion = f"{response.explanation}\n\n```cpp\n{response.fixed_code}\n```"
        # return violation
        try:
            prompt = build_user_prompt(violation)
            result = self.client.complete(
                prompt, system=SYSTEM_PROMPT, temperature=0.1, max_tokens=512
            )
            cleaned = self._strip_fences(result.text)
            response = self._parse_review_response(cleaned)
            fixed_code = self._strip_code_fence(response.fixed_code)

            if response is None:
                logger.warning(
                    "Could not parse LLM output for %s. Raw: %r",
                    violation.short(),
                    result.text[:200],
                )
                return violation

            violation.fix_suggestion = f"{response.explanation}\n\n```cpp\n{fixed_code}\n```"
        except Exception as e:
            logger.warning("LLM review failed: %s", e)

        return violation

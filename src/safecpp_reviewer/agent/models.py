"""Models for the fix verification loop."""

from __future__ import annotations

from pydantic import BaseModel, Field

from safecpp_reviewer.analyzer.models import Violation


class VerificationResult(BaseModel):
    """Outcome of attempting to verify an LLM-proposed fix.

    Attributes:
        resolved: ``True`` if the original violation no longer appears after
            the fix is applied and clang-tidy is re-run.
        new_violations: Any violations the fix introduced (regressions).
        error: Populated if the verification process itself failed
            (e.g. patch could not be applied, clang-tidy crashed).
    """

    resolved: bool
    new_violations: list[Violation] = Field(default_factory=list)
    error: str | None = None

    @property
    def regressed(self) -> bool:
        """The fix introduced new problems even if it resolved the original."""
        return len(self.new_violations) > 0

"""Pydantic model for a single static analysis rule."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Rule(BaseModel):
    """A single static analysis rule with documentation.

    Attributes:
        rule_id: Full tool-prefixed identifier matching :attr:`Violation.rule_id`,
            e.g. ``"clang-tidy:cppcoreguidelines-pro-type-cstyle-cast"``.
        short_name: Human-readable one-line summary of the rule.
        category: Grouping, e.g. ``"cppcoreguidelines"``, ``"MISRA"``, ``"AUTOSAR"``.
        rationale: Why this rule exists — surfaced in the LLM prompt to ground fixes.
        bad_example: Optional snippet showing a violating pattern.
        good_example: Optional snippet showing the corrected pattern.
        references: Links or document citations (e.g. MISRA C++ §6.7.2).
    """

    rule_id: str
    short_name: str
    category: str
    rationale: str
    bad_example: str | None = None
    good_example: str | None = None
    references: list[str] = Field(default_factory=list)

    def for_prompt(self) -> str:
        """Compact rule summary suitable for LLM context injection."""
        lines = [
            f"{self.rule_id} - {self.short_name}",
            f"Category: {self.category}",
            f"Rationale: {self.rationale}",
        ]
        if self.bad_example:
            lines.append(f"Bad example:\n{self.bad_example}")
        if self.good_example:
            lines.append(f"Good example:\n{self.good_example}")
        return "\n".join(lines)

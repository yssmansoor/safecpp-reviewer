"""Core data models for static analysis violations."""

import typing
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class ReviewResponse(BaseModel):
    """LLM review response with explanation and corrected code."""

    explanation: str  # why this is a violation, in plain English
    fixed_code: str  # the corrected snippet


class ChunkReview(BaseModel):
    """LLM review for one violation inside a chunk response."""

    violation_index: int = Field(ge=0)
    explanation: str
    fixed_code: str


class ChunkReviewResponse(BaseModel):
    """LLM chunk review response containing one review per violation."""

    reviews: list[ChunkReview]


class Violation(BaseModel):
    """A single static analysis violation found in a C++ source file.

    Attributes:
        file: Absolute or relative path to the source file.
        line: 1-based line number where the violation occurs.
        column: 1-based column number (None if not reported by tool).
        rule_id: Tool-prefixed rule identifier, e.g. "clang-tidy:cppcoreguidelines-pro-type-reinterpret-cast".
        severity: Normalized severity level across tools.
        message: Human-readable description of the violation.
        tool: Which static analysis tool produced this violation.
        category: Optional grouping, e.g. "MISRA", "AUTOSAR", "cppcoreguidelines".
        code_snippet: Optional source line(s) surrounding the violation.
        fix_suggestion: Optional LLM-generated fix (populated in Phase 3+).
    """

    file: Path
    line: int = Field(ge=1)
    column: int | None = Field(default=None, ge=1)
    rule_id: str
    severity: Literal["error", "warning", "note", "style"]
    message: str
    tool: Literal["clang-tidy", "cppcheck"]
    category: str | None = None
    code_snippet: str | None = None
    fix_suggestion: str | None = None

    # def short(self) -> str:
    #     """One-line summary for logging and CLI output."""
    #     col = f":{self.column}" if self.column else ""
    #     base = f"[{self.tool}] {self.file}:{self.line}{col} ({self.rule_id}): {self.message}"
    #     if self.code_snippet:
    #         base += f"\n{self.code_snippet}"
    #     return base

    def short(self, color: bool = False) -> str:
        """Multi-line summary for logging and CLI output.

        Args:
            color: If True, wrap severity and rule_id in rich markup tags.
        """
        sev_colors: typing.Final = {
            "error": "bold red",
            "warning": "yellow",
            "style": "cyan",
            "note": "dim",
        }

        if color:
            sev = f"[{sev_colors[self.severity]}]{self.severity.upper():<7}[/]"
            rule = f"[bold]{self.rule_id}[/bold]"
            tool = f"[magenta]{self.tool}[/magenta]"
        else:
            sev = f"{self.severity.upper():<7}"
            rule = self.rule_id
            tool = self.tool

        col: typing.Final = f":{self.column}" if self.column else ""
        category: typing.Final = f" <{self.category}>" if self.category else ""

        header: typing.Final = f"{sev} [{tool}]{category} {self.file}:{self.line}{col}"
        body: typing.Final = f"  → {rule}\n  {self.message}"

        out = f"{header}\n{body}"
        if self.code_snippet:
            out += f"\n{self.code_snippet}"
        if self.fix_suggestion:
            out += f"\n  ↪ Fix: {self.fix_suggestion}"
        return out

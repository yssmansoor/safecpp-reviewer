# from pathlib import Path
# from pydantic import BaseModel
# from typing import Literal, Optional


# class Violation(BaseModel):
#     file: Path
#     line: int
#     column: Optional[int]
#     rule_id: str          # e.g. "MISRA-C++:2023-6.7.2" or "clang-tidy:cppcoreguidelines-*"
#     severity: Literal["error", "warning", "note", "style"]
#     message: str
#     tool: Literal["clang-tidy", "cppcheck"]
#     category: Optional[str]
#     code_snippet: Optional[str]
#     fix_suggestion: Optional[str]

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

SeverityLevel = Literal[
    "error",
    "warning",
    "style",
    "note",
    "information",  # cppcheck emits this
]


class Violation(BaseModel):
    """Describes a static analysis violation detected in a source file."""

    tool: str

    file_path: Path
    line: int
    column: int | None = None

    severity: str
    rule_id: str

    message: str

    category: str | None = None

    code_snippet: str | None = None

    source_hash: str | None = None

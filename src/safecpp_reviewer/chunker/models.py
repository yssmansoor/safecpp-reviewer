from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class Chunk(BaseModel):
    """Represents a chunk of code within a file."""

    file: Path
    start_line: int
    end_line: int
    content: str
    chunk_type: Literal["function", "class", "namespace", "global", "raw"]
    name: str | None = None  # e.g. "process" or "Derived::tick"
    token_estimate: int  # rough count for budgeting

    def contains(self, line: int) -> bool:
        """Returns true if the chunk contains the given line number."""
        return self.start_line <= line <= self.end_line

    def __contains__(self, item: int) -> bool:
        if isinstance(item, int):
            return self.contains(item)
        raise TypeError(
            f"Chunk only supports containment checks for line numbers (ints), not {type(item)}"
        )

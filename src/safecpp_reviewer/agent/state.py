from pathlib import Path
from typing import TypedDict

from safecpp_reviewer.analyzer.models import Violation
from safecpp_reviewer.chunker.models import Chunk


class ReviewerState(TypedDict):
    """Represents the state of a code review."""

    source_file: Path
    violations: list[Violation]
    chunks: list[Chunk]
    failed_reviews: list[Violation]  # for retry node later
    output_formats: list[str]
    output_path: Path | None
    retry_count: int

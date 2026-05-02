"""Static analysis orchestration layer.

Provides a single :func:`run_all` entry point that runs both clang-tidy and
cppcheck on a file and merges results into one deduplicated list.
"""

from __future__ import annotations

import typing
from pathlib import Path
from typing import TYPE_CHECKING

from safecpp_reviewer.analyzer.clang_tidy import ClangTidyRunner
from safecpp_reviewer.analyzer.cppcheck import CppcheckRunner
from safecpp_reviewer.analyzer.filter import filter_real_violations
from safecpp_reviewer.analyzer.models import Violation
from safecpp_reviewer.analyzer.snippet import extract_snippet
from safecpp_reviewer.chunker.models import Chunk
from safecpp_reviewer.chunker.parser import CppParser

if TYPE_CHECKING:
    from safecpp_reviewer.agent.reviewer import ViolationReviewer

MAX_CHUNK_REVIEW_VIOLATIONS: typing.Final = 5

_META_RULES = {
    "clang-tidy:clang-diagnostic-error",
    "clang-tidy:clang-tidy-nolint",
    "clang-tidy:bugprone-reserved-identifier",  # often noise on system headers
    "clang-tidy:clang-diagnostic-fatal-error",
}


def _is_real_violation(v: Violation) -> bool:
    return v.rule_id not in _META_RULES


def run_all(
    source_file: Path,
    clang_tidy_checks: str = "cppcoreguidelines-*,modernize-*,readability-*,bugprone-*",
    cppcheck_enable: str = "all",
    extra_compiler_args: list[str] | None = None,
    reviewer: ViolationReviewer | None = None,
) -> list[Violation]:
    """Run clang-tidy and cppcheck on *source_file* and merge the results.

    Violations are deduplicated by (tool, file, line, rule_id) so that the
    same issue reported twice (e.g. if clang-tidy is run multiple times) does
    not appear more than once.

    Args:
        source_file: Path to the C++ file to analyse.
        clang_tidy_checks: Check glob forwarded to :class:`ClangTidyRunner`.
        cppcheck_enable: Enable string forwarded to :class:`CppcheckRunner`.
        extra_compiler_args: Compiler flags for clang-tidy (default: ``["-std=c++17"]``).

    Returns:
        Merged, deduplicated list of :class:`Violation` objects sorted by
        (file, line, column).
    """
    compiler_args: typing.Final = extra_compiler_args or ["-std=c++17"]

    ct_runner: typing.Final = ClangTidyRunner(checks=clang_tidy_checks, extra_args=compiler_args)
    cc_runner: typing.Final = CppcheckRunner(enable=cppcheck_enable)

    ct_violations: list[Violation] = []
    cc_violations: list[Violation] = []

    try:
        ct_violations = ct_runner.run(source_file)
    except RuntimeError as e:
        import logging

        logging.getLogger(__name__).warning("clang-tidy unavailable: %s", e)

    try:
        cc_violations = cc_runner.run(source_file)
    except RuntimeError as e:
        import logging

        logging.getLogger(__name__).warning("cppcheck unavailable: %s", e)

    all_violations: typing.Final = ct_violations + cc_violations

    # Deduplicate
    seen: typing.Final[set[tuple[str, str, int, str]]] = set()
    unique: list[Violation] = []
    for v in all_violations:
        key = (v.tool, str(v.file), v.line, v.rule_id)
        if key not in seen:
            seen.add(key)
            unique.append(v)

    unique = filter_real_violations(unique)

    for v in unique:
        v.code_snippet = extract_snippet(v)

    if reviewer is not None:
        unique = _review_by_chunk(source_file, unique, reviewer)

    return sorted(unique, key=lambda v: (str(v.file), v.line, v.column or 0))


def _review_by_chunk(
    source_file: Path,
    violations: list[Violation],
    reviewer: ViolationReviewer,
) -> list[Violation]:
    """Review violations with one LLM call per containing source chunk."""
    try:
        chunks: typing.Final = CppParser().parse_file(source_file)
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(
            "Tree-sitter chunking failed; falling back to per-violation review: %s",
            e,
        )
        return [reviewer.review(v) for v in violations]

    chunk_groups: typing.Final[list[tuple[Chunk, list[Violation]]]] = []
    standalone: typing.Final[list[Violation]] = []
    chunks_by_specificity: typing.Final = sorted(
        chunks,
        key=lambda chunk: (chunk.end_line - chunk.start_line, chunk.start_line, chunk.end_line),
    )

    for violation in violations:
        chunk = next(
            (candidate for candidate in chunks_by_specificity if violation.line in candidate), None
        )
        if chunk is None:
            standalone.append(violation)
            continue

        group = next((group for group in chunk_groups if group[0] is chunk), None)
        if group is None:
            chunk_groups.append((chunk, [violation]))
        else:
            group[1].append(violation)

    for chunk, grouped_violations in chunk_groups:
        if len(grouped_violations) > MAX_CHUNK_REVIEW_VIOLATIONS:
            for violation in grouped_violations:
                reviewer.review(violation)
            continue

        reviewer.review_chunk(chunk, grouped_violations)

    for violation in standalone:
        reviewer.review(violation)

    return violations


__all__ = ["run_all", "Violation", "ClangTidyRunner", "CppcheckRunner"]

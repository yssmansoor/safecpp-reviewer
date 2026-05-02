"""Filter out meta-diagnostics from analyzer output.

clang-tidy emits diagnostics that aren't actually rule violations:

* ``clang-diagnostic-error`` — the file failed to compile (usually due to
  missing include paths). Asking the LLM to fix this is asking it to fix
  build configuration, not code.
* ``clang-tidy-nolint`` — meta-diagnostic about NOLINT comment usage.
* ``clang-diagnostic-fatal-error`` — same category as -error.

These should be filtered before the LLM ever sees them, otherwise the
verifier produces meaningless results dominated by build-system noise.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from safecpp_reviewer.analyzer.models import Violation

logger = logging.getLogger(__name__)

# Exact rule_id matches that should always be filtered.
_META_RULES: frozenset[str] = frozenset(
    {
        "clang-tidy:clang-diagnostic-error",
        "clang-tidy:clang-diagnostic-fatal-error",
        "clang-tidy:clang-diagnostic-warning",
        "clang-tidy:clang-tidy-nolint",
    }
)

# Prefixes — anything starting with these is treated as a build-system or
# pre-processor diagnostic, not a real violation.
_META_PREFIXES: tuple[str, ...] = ("clang-tidy:clang-diagnostic-",)


def is_real_violation(v: Violation) -> bool:
    """Return True if *v* represents a real code rule violation.

    Meta-diagnostics from clang-tidy (compilation errors, NOLINT meta) are
    rejected because they signal build-configuration issues, not code
    problems the LLM should be asked to fix.
    """
    return v.rule_id not in _META_RULES and not any(v.rule_id.startswith(p) for p in _META_PREFIXES)


def filter_real_violations(violations: Iterable[Violation]) -> list[Violation]:
    """Return only the violations that represent real code rule findings."""
    real: list[Violation] = []
    skipped = 0
    for v in violations:
        if is_real_violation(v):
            real.append(v)
        else:
            skipped += 1
    if skipped:
        logger.info(
            "filter: removed %d meta-diagnostic(s) (clang-diagnostic-* etc.)",
            skipped,
        )
    return real

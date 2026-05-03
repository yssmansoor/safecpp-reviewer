"""Helpers for filtering violations to changed lines only.

Used by the GitHub Action so review comments are only posted on lines the
PR actually modified, not pre-existing issues in the file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from safecpp_reviewer.analyzer.models import Violation

logger = logging.getLogger(__name__)


def load_changed_lines(path: Path) -> dict[str, set[int]]:
    """Load a ``{filename: [lines]}`` JSON map into ``{filename: set(lines)}``.

    The JSON is produced by ``scripts/changed_lines.py`` and shaped like::

        {"src/foo.cpp": [12, 13, 14, 27],
         "include/bar.hpp": [3, 4]}
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, set[int]] = {}
    for fname, lines in raw.items():
        # Normalise to forward slashes so lookups match across platforms
        out[fname.replace("\\", "/")] = {int(line) for line in lines}
    return out


def filter_to_changed_lines(
    violations: list[Violation],
    changed: dict[str, set[int]],
) -> list[Violation]:
    """Return only violations whose ``(file, line)`` was modified in the PR.

    File matching is suffix-based — the violation file may be an absolute
    path while the diff uses a repo-relative path.  We try both forms.
    """
    if not changed:
        return violations

    kept: list[Violation] = []
    skipped = 0
    for v in violations:
        v_path = str(v.file).replace("\\", "/")
        matched_lines: set[int] | None = None
        for changed_path, lines in changed.items():
            if v_path == changed_path or v_path.endswith("/" + changed_path):
                matched_lines = lines
                break
        if matched_lines is None:
            skipped += 1
            continue
        if v.line in matched_lines:
            kept.append(v)
        else:
            skipped += 1

    logger.info(
        "changed-lines filter: kept %d, dropped %d (not in PR diff)",
        len(kept),
        skipped,
    )
    return kept

#!/usr/bin/env python3
"""Compute the set of lines changed in a PR diff.

Reads the unified diff between two git commits and emits a JSON map of
``{filename: [line_numbers]}`` covering only added/modified lines on the
*new* (right) side.  Used by the safecpp GitHub Action to limit review
comments to the PR's actual changes.

Usage::

    python changed_lines.py <base_sha> <head_sha> > changed.json
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

# Hunk header: @@ -<old>,<old_count> +<new>,<new_count> @@ ...
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
# File header in unified diff: +++ b/path/to/file
_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$")


def _diff(base: str, head: str) -> str:
    """Run ``git diff base..head`` and return the unified diff."""
    result = subprocess.run(
        ["git", "diff", "--unified=0", f"{base}..{head}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def parse_diff(diff: str) -> dict[str, list[int]]:
    """Parse a unified diff into ``{filename: [lines added/modified]}``."""
    out: dict[str, list[int]] = {}
    current_file: str | None = None
    current_line: int = 0

    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            m = _FILE_RE.match(raw)
            if m and m.group(1) != "/dev/null":
                current_file = m.group(1)
                out.setdefault(current_file, [])
            else:
                current_file = None
            continue

        if current_file is None:
            continue

        if raw.startswith("@@"):
            m = _HUNK_RE.match(raw)
            if not m:
                continue
            current_line = int(m.group(1))
            continue

        # Lines starting with "+" but not "+++" are additions on the new side
        if raw.startswith("+") and not raw.startswith("+++"):
            out[current_file].append(current_line)
            current_line += 1
        elif raw.startswith("-"):
            # Deletion — does not advance the new-side line counter
            continue
        else:
            # Context line — advances new-side counter
            current_line += 1

    # Drop any files with no added/modified lines (pure deletions)
    return {f: ls for f, ls in out.items() if ls}


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: changed_lines.py <base_sha> <head_sha>", file=sys.stderr)
        sys.exit(1)

    base, head = sys.argv[1], sys.argv[2]
    diff = _diff(base, head)
    parsed = parse_diff(diff)
    json.dump(parsed, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

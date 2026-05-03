"""GitHub PR review-comment formatter.

Renders a ``list[Violation]`` as the JSON payload the safecpp Action posts
back to a pull request via ``pulls.createReviewComment``.

Output shape::

    {
      "comments": [
        {
          "path": "src/foo.cpp",
          "line": 42,
          "side": "RIGHT",
          "body": "**[clang-tidy]** ...\\n\\n```suggestion\\n...\\n```"
        }
      ]
    }

The ``body`` uses GitHub's native ``suggestion`` code-fence so reviewers
can apply the LLM's fix with one click.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from safecpp_reviewer.analyzer.models import Violation

# Reuses the same fence regex pattern from the verifier
_FENCE_RE = re.compile(r"```(?:cpp|c\+\+)?\s*\n(.*?)```", re.DOTALL)


def _extract_fix_code(fix_suggestion: str | None) -> str | None:
    """Pull the C++ block out of a fix_suggestion string."""
    if not fix_suggestion:
        return None
    fences = _FENCE_RE.findall(fix_suggestion)
    if fences:
        return fences[-1].strip()
    return None


def _format_body(v: Violation) -> str:
    """Build a markdown comment body for one violation."""
    sev_emoji = {
        "error": "🛑",
        "warning": "⚠️",
        "style": "💡",
        "note": "📝",
    }
    icon = sev_emoji.get(v.severity, "•")

    parts = [
        f"{icon} **[{v.tool}]** `{v.rule_id}`",
        "",
        v.message,
    ]

    if v.fix_suggestion:
        # Strip the fence so we can put the code inside a `suggestion` block
        code = _extract_fix_code(v.fix_suggestion)
        if code:
            parts.extend(
                [
                    "",
                    "<details><summary>Suggested fix</summary>",
                    "",
                    "```suggestion",
                    code,
                    "```",
                    "</details>",
                ]
            )
        else:
            # Fall back to the explanation block as plain markdown
            parts.extend(["", v.fix_suggestion])

    return "\n".join(parts)


def render_github(
    violations: list[Violation],
    output_path: Path | None = None,
    repo_root: Path | None = None,
) -> str:
    """Render *violations* as a GitHub review-comment JSON payload.

    Args:
        violations: List of violations to post.
        output_path: If given, write the JSON here.  Otherwise return as a string.
        repo_root: If given, paths in the output are made relative to this
            directory.  Required when violation paths are absolute.

    Returns:
        The JSON payload as a string.
    """
    comments = []
    for v in violations:
        path = v.file
        if repo_root is not None:
            try:
                path = Path(v.file).resolve().relative_to(repo_root.resolve())
            except ValueError:
                # Outside repo root — skip
                path = v.file
                # continue

        comments.append(
            {
                "path": str(path).replace("\\", "/"),
                "line": v.line,
                "side": "RIGHT",
                "body": _format_body(v),
            }
        )

    payload = {"comments": comments}
    text = json.dumps(payload, indent=2)

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")

    return text

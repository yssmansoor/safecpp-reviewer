"""HTML report generator for static analysis violations.

Renders a list of :class:`Violation` objects as a self-contained HTML file
with severity badges, syntax-highlighted code snippets, and LLM fix suggestions.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from safecpp_reviewer.analyzer.models import Violation

_CSS = """
* { box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0d1117;
    color: #c9d1d9;
    margin: 0;
    padding: 2rem;
    line-height: 1.5;
}
.container { max-width: 1100px; margin: 0 auto; }
h1 { color: #f0f6fc; margin-bottom: 0.25rem; }
.subtitle { color: #8b949e; margin-bottom: 2rem; font-size: 0.9rem; }
.summary {
    display: flex;
    gap: 1rem;
    margin-bottom: 2rem;
    flex-wrap: wrap;
}
.stat {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 0.75rem 1.25rem;
    min-width: 120px;
}
.stat-label { font-size: 0.75rem; color: #8b949e; text-transform: uppercase; }
.stat-value { font-size: 1.5rem; font-weight: 600; color: #f0f6fc; }
.violation {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    margin-bottom: 1rem;
    overflow: hidden;
}
.violation-header {
    padding: 0.75rem 1rem;
    border-bottom: 1px solid #30363d;
    display: flex;
    align-items: center;
    gap: 0.75rem;
    flex-wrap: wrap;
}
.badge {
    padding: 0.15rem 0.6rem;
    border-radius: 12px;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
}
.sev-error   { background: #5a1d1d; color: #ff7b72; }
.sev-warning { background: #5a4a1d; color: #d29922; }
.sev-style   { background: #1d3a5a; color: #58a6ff; }
.sev-note    { background: #30363d; color: #8b949e; }
.tool-tag { color: #8b949e; font-size: 0.85rem; }
.rule-id { color: #d2a8ff; font-family: monospace; font-size: 0.85rem; }
.violation-body { padding: 1rem; }
.message { color: #f0f6fc; margin-bottom: 0.75rem; font-weight: 500; }
.code {
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 0.75rem;
    overflow-x: auto;
    font-family: "SF Mono", Consolas, monospace;
    font-size: 0.85rem;
    white-space: pre;
    color: #c9d1d9;
}
.code .marker { color: #ff7b72; font-weight: 700; }
.fix-section {
    margin-top: 1rem;
    padding: 0.75rem;
    background: #0f2419;
    border-left: 3px solid #2ea043;
    border-radius: 4px;
}
.fix-label {
    color: #2ea043;
    font-weight: 600;
    font-size: 0.85rem;
    margin-bottom: 0.5rem;
}
.fix-text { color: #c9d1d9; white-space: pre-wrap; font-size: 0.9rem; }
.empty {
    text-align: center;
    padding: 3rem;
    color: #8b949e;
}
.fix-section {
    margin-top: 1rem;
    padding: 1rem;
    background: #1a2820;
    border-left: 3px solid #2ea043;
    border-radius: 4px;
}
.fix-label {
    color: #2ea043;
    font-weight: 600;
    font-size: 0.85rem;
    margin-bottom: 0.75rem;
}
.fix-prose {
    color: #c9d1d9;
    margin: 0.5rem 0;
    font-size: 0.9rem;
    line-height: 1.6;
}
.fix-section .code {
    background: #0d1117;
    border: 1px solid #30363d;
    margin-top: 0.5rem;
    margin-bottom: 0.5rem;
}
.location {
    color: #58a6ff;
    font-family: monospace;
    font-size: 0.85rem;
    text-decoration: none;
}
.location:hover { text-decoration: underline; }
.back-link {
    display: inline-block;
    color: #58a6ff;
    text-decoration: none;
    margin-bottom: 1.5rem;
    font-size: 0.9rem;
}
.back-link:hover { text-decoration: underline; }
"""


def _severity_badge(severity: str) -> str:
    return f'<span class="badge sev-{severity}">{severity}</span>'


def _format_snippet(snippet: str | None) -> str:
    if not snippet:
        return ""
    escaped_lines = []
    for line in snippet.splitlines():
        escaped = html.escape(line)
        if line.startswith(">>>"):
            escaped = f'<span class="marker">{escaped}</span>'
        escaped_lines.append(escaped)
    return f'<pre class="code">{chr(10).join(escaped_lines)}</pre>'


def _render_violation(v: Violation) -> str:
    file_url = f"file://{Path(v.file).resolve()}"
    header = (
        f'<div class="violation-header">'
        f"{_severity_badge(v.severity)}"
        f'<span class="tool-tag">{html.escape(v.tool)}</span>'
        f'<a class="location" href="{html.escape(file_url)}">'
        f"{html.escape(str(v.file))}:{v.line}"
        f"{':' + str(v.column) if v.column else ''}"
        f"</a>"
        f'<span class="rule-id">{html.escape(v.rule_id)}</span>'
        f"</div>"
    )

    body = f'<div class="message">{html.escape(v.message)}</div>'
    body += _format_snippet(v.code_snippet)

    if v.fix_suggestion:
        body += (
            '<div class="fix-section">'
            '<div class="fix-label">↪ Suggested Fix</div>'
            f"{_format_fix(v.fix_suggestion)}"
            "</div>"
        )

    return f'<div class="violation">{header}<div class="violation-body">{body}</div></div>'


def render_html(
    violations: list[Violation],
    output_path: Path,
    title: str = "safecpp-reviewer Report",
    back_link: str | None = None,  # NEW
) -> Path:
    """Write *violations* as a self-contained HTML report to *output_path*.

    Args:
        violations: List of violations to render.
        output_path: Where to write the HTML file.
        title: Page title shown at the top of the report.

    Returns:
        The output path (for chaining).
    """

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sev_counts = Counter(v.severity for v in violations)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    stats_html = "".join(
        f'<div class="stat">'
        f'<div class="stat-label">{label}</div>'
        f'<div class="stat-value">{value}</div>'
        f"</div>"
        for label, value in [
            ("Total", len(violations)),
            ("Errors", sev_counts.get("error", 0)),
            ("Warnings", sev_counts.get("warning", 0)),
            ("Style", sev_counts.get("style", 0)),
        ]
    )

    if violations:
        body_html = "\n".join(_render_violation(v) for v in violations)
    else:
        body_html = '<div class="empty">No violations found.</div>'

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{html.escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="container">
<h1>{html.escape(title)}</h1>
<div class="subtitle">Generated {timestamp}</div>
<div class="summary">{stats_html}</div>
{body_html}
</div>
</body>
</html>
"""
    nav_html = (
        f'<a class="back-link" href="{html.escape(back_link)}">← Back to index</a>'
        if back_link
        else ""
    )

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{html.escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="container">
{nav_html}
<h1>{html.escape(title)}</h1>
<div class="subtitle">Generated {timestamp}</div>
<div class="summary">{stats_html}</div>
{body_html}
</div>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_doc, encoding="utf-8")

    return output_path


# Multi-line fenced code: ```cpp\n...\n``` or ```\n...\n```
_FENCE_RE = re.compile(r"```(?:cpp|c\+\+)?\s*\n?(.*?)```", re.DOTALL)

# Inline code with optional language tag: `cpp something` or `something`
_INLINE_RE = re.compile(r"`(?:cpp\s+|c\+\+\s+)?([^`\n]+)`")


def _format_fix(fix: str) -> str:
    """Render fix text into prose + code blocks, robust to LLM formatting variation."""
    if not fix or not fix.strip():
        return ""

    parts: list[str] = []
    last_end = 0

    # First pass: handle proper multi-line fenced blocks
    matches = list(_FENCE_RE.finditer(fix))

    if matches:
        for m in matches:
            prose = fix[last_end : m.start()].strip()
            if prose:
                parts.append(f'<div class="fix-prose">{html.escape(prose)}</div>')

            code = m.group(1).strip()
            if code:  # skip empty fences (this fixes the black bars)
                parts.append(f'<pre class="code">{html.escape(code)}</pre>')

            last_end = m.end()

        trailing = fix[last_end:].strip()
        if trailing:
            parts.append(f'<div class="fix-prose">{html.escape(trailing)}</div>')

    else:
        # Fallback: handle inline `cpp ...` patterns or plain "cpp ..." text
        cleaned = fix.strip()

        # Strip inline backticks if the whole thing is wrapped: `cpp something`
        inline_match = _INLINE_RE.fullmatch(cleaned)
        if inline_match:
            parts.append(f'<pre class="code">{html.escape(inline_match.group(1).strip())}</pre>')
        # Detect "cpp <code>" pattern even without backticks
        elif cleaned.lower().startswith(("cpp ", "c++ ")):
            code = cleaned.split(" ", 1)[1].strip()
            parts.append(f'<pre class="code">{html.escape(code)}</pre>')
        else:
            # Look for embedded inline blocks
            last = 0
            for m in _INLINE_RE.finditer(cleaned):
                prose = cleaned[last : m.start()].strip()
                if prose:
                    parts.append(f'<div class="fix-prose">{html.escape(prose)}</div>')
                code = m.group(1).strip()
                if code:
                    parts.append(f'<pre class="code">{html.escape(code)}</pre>')
                last = m.end()
            trailing = cleaned[last:].strip()
            if trailing:
                parts.append(f'<div class="fix-prose">{html.escape(trailing)}</div>')

    return "\n".join(parts) if parts else f'<div class="fix-prose">{html.escape(fix)}</div>'

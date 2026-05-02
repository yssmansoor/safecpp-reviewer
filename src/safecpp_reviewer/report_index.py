"""Index page generator for multi-file reports.

Generates a top-level ``index.html`` that lists every per-file report with
violation counts and severity badges, and a back-link injection helper for
the per-file pages.

Typical usage::

    from safecpp_reviewer.report import render_html
    from safecpp_reviewer.report_index import render_index, ReportEntry

    entries = []
    for src in source_files:
        violations = run_review(src)
        out = reports_dir / f"{src.name}.html"
        render_html(violations, out, title=src.name, back_link="index.html")
        entries.append(ReportEntry(
            source=src,
            report_path=out,
            violations=violations,
        ))

    render_index(entries, reports_dir / "index.html")
"""

from __future__ import annotations

import html
import typing
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from safecpp_reviewer.analyzer.models import Violation


@dataclass
class ReportEntry:
    """One row in the index — links a source file to its rendered report."""

    source: Path
    report_path: Path
    violations: list[Violation]

    @property
    def severity_counts(self) -> Counter[str]:
        return Counter(v.severity for v in self.violations)

    @property
    def total(self) -> int:
        return len(self.violations)

    @property
    def fixed(self) -> int:
        return sum(1 for v in self.violations if v.fix_suggestion)


_INDEX_CSS: typing.Final = """
* { box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0d1117; color: #c9d1d9;
    margin: 0; padding: 2rem; line-height: 1.5;
}
.container { max-width: 1100px; margin: 0 auto; }
h1 { color: #f0f6fc; margin-bottom: 0.25rem; }
.subtitle { color: #8b949e; margin-bottom: 2rem; font-size: 0.9rem; }
.summary {
    display: flex; gap: 1rem; margin-bottom: 2rem; flex-wrap: wrap;
}
.stat {
    background: #161b22; border: 1px solid #30363d;
    border-radius: 6px; padding: 0.75rem 1.25rem; min-width: 130px;
}
.stat-label { font-size: 0.75rem; color: #8b949e; text-transform: uppercase; }
.stat-value { font-size: 1.5rem; font-weight: 600; color: #f0f6fc; }
table {
    width: 100%; border-collapse: collapse;
    background: #161b22; border: 1px solid #30363d; border-radius: 6px;
    overflow: hidden;
}
th, td {
    padding: 0.75rem 1rem; text-align: left;
    border-bottom: 1px solid #30363d;
}
th {
    background: #1c222b; color: #f0f6fc;
    font-weight: 600; font-size: 0.85rem;
    text-transform: uppercase; letter-spacing: 0.05em;
}
tr:last-child td { border-bottom: none; }
tr:hover { background: #1c222b; }
td.numeric { text-align: right; font-variant-numeric: tabular-nums; }
a.file-link {
    color: #58a6ff; text-decoration: none; font-family: monospace;
}
a.file-link:hover { text-decoration: underline; }
.badge {
    display: inline-block;
    padding: 0.1rem 0.5rem; margin-right: 0.25rem;
    border-radius: 10px; font-size: 0.7rem; font-weight: 600;
    font-family: monospace;
}
.b-error   { background: #5a1d1d; color: #ff7b72; }
.b-warning { background: #5a4a1d; color: #d29922; }
.b-style   { background: #1d3a5a; color: #58a6ff; }
.b-note    { background: #30363d; color: #8b949e; }
.b-zero    { background: #21262d; color: #484f58; }
.fix-cell { color: #2ea043; font-weight: 500; }
.empty-row td {
    text-align: center; padding: 2rem; color: #8b949e;
}
.search-box {
    width: 100%; padding: 0.5rem 0.75rem; margin-bottom: 1rem;
    background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
    color: #c9d1d9; font-size: 0.9rem;
}
.search-box:focus { outline: none; border-color: #58a6ff; }
"""

_INDEX_JS: typing.Final = """
document.addEventListener('DOMContentLoaded', () => {
    const search = document.getElementById('search');
    const rows = document.querySelectorAll('tbody tr');
    if (!search) return;
    search.addEventListener('input', (e) => {
        const q = e.target.value.toLowerCase();
        rows.forEach(row => {
            const file = row.dataset.file || '';
            row.style.display = file.includes(q) ? '' : 'none';
        });
    });
});
"""


def _badge(severity: str, count: int) -> str:
    cls: typing.Final = f"b-{severity}" if count > 0 else "b-zero"
    return f'<span class="badge {cls}">{severity[:3]} {count}</span>'


def _row(entry: ReportEntry, root: Path) -> str:
    rel_report: typing.Final = (
        entry.report_path.relative_to(root)
        if root in entry.report_path.parents
        else entry.report_path
    )
    counts: typing.Final = entry.severity_counts

    badges: typing.Final = (
        _badge("error", counts.get("error", 0))
        + _badge("warning", counts.get("warning", 0))
        + _badge("style", counts.get("style", 0))
        + _badge("note", counts.get("note", 0))
    )

    fix_cell: typing.Final = (
        f'<span class="fix-cell">{entry.fixed}/{entry.total}</span>' if entry.total else "—"
    )

    return (
        f'<tr data-file="{html.escape(str(entry.source).lower())}">'
        f'<td><a class="file-link" href="{html.escape(str(rel_report))}">'
        f"{html.escape(str(entry.source))}</a></td>"
        f'<td class="numeric">{entry.total}</td>'
        f"<td>{badges}</td>"
        f'<td class="numeric">{fix_cell}</td>'
        f"</tr>"
    )


def render_index(
    entries: list[ReportEntry],
    output_path: Path,
    title: str = "safecpp-reviewer — Index",
) -> Path:
    """Render the top-level index page listing every per-file report.

    Args:
        entries: One :class:`ReportEntry` per analyzed file.
        output_path: Where to write ``index.html``.
        title: Page title shown at the top.

    Returns:
        The output path (for chaining).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    root: typing.Final = output_path.parent

    # Sort: files with the most errors first, then by total violations
    entries_sorted: typing.Final = sorted(
        entries,
        key=lambda e: (
            -e.severity_counts.get("error", 0),
            -e.total,
            str(e.source),
        ),
    )

    total_files: typing.Final = len(entries)
    total_violations: typing.Final = sum(e.total for e in entries)
    total_errors: typing.Final = sum(e.severity_counts.get("error", 0) for e in entries)
    total_fixed: typing.Final = sum(e.fixed for e in entries)
    timestamp: typing.Final = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    stats_html: typing.Final = "".join(
        f'<div class="stat">'
        f'<div class="stat-label">{label}</div>'
        f'<div class="stat-value">{value}</div>'
        f"</div>"
        for label, value in [
            ("Files", total_files),
            ("Total violations", total_violations),
            ("Errors", total_errors),
            ("LLM fixes", total_fixed),
        ]
    )

    if entries_sorted:
        rows_html = "\n".join(_row(e, root) for e in entries_sorted)
    else:
        rows_html = '<tr class="empty-row"><td colspan="4">No files analyzed.</td></tr>'

    html_doc: typing.Final = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{html.escape(title)}</title>
<style>{_INDEX_CSS}</style>
</head>
<body>
<div class="container">
<h1>{html.escape(title)}</h1>
<div class="subtitle">Generated {timestamp}</div>
<div class="summary">{stats_html}</div>
<input id="search" class="search-box" type="text" placeholder="Filter files...">
<table>
<thead>
<tr><th>File</th><th>Total</th><th>By Severity</th><th>LLM-fixed</th></tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>
</div>
<script>{_INDEX_JS}</script>
</body>
</html>
"""
    output_path.write_text(html_doc, encoding="utf-8")
    return output_path

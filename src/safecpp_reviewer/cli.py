"""Command-line interface for safecpp-reviewer.

Usage::

    safecpp review path/to/file.cpp --format html,terminal --output report.html
    safecpp review path/to/file.cpp --no-llm
    safecpp review path/to/file.cpp --format json --output violations.json
"""

from __future__ import annotations

import enum
import json
import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from safecpp_reviewer.agent.reviewer import ViolationReviewer
from safecpp_reviewer.analyzer import run_all
from safecpp_reviewer.analyzer.models import Violation
from safecpp_reviewer.llm.client import LlamaCppClient
from safecpp_reviewer.report import render_html

app = typer.Typer(
    name="safecpp",
    help="Agentic LLM-powered C++ safety reviewer (MISRA / AUTOSAR / cppcoreguidelines).",
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True)

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)


class OutputFormat(enum.StrEnum):
    terminal = "terminal"
    json = "json"
    html = "html"


# ---------------------------------------------------------------------------
# Output renderers
# ---------------------------------------------------------------------------


def _render_terminal(violations: list[Violation]) -> None:
    if not violations:
        console.print("[green]✓ No violations found.[/]")
        return

    border_for = {"error": "red", "warning": "yellow", "style": "cyan", "note": "dim"}

    for v in violations:
        col = f":{v.column}" if v.column else ""
        title = f"[{v.severity.upper()}] {v.file}:{v.line}{col}"

        # Header panel without fix
        body = f"[bold]{v.message}[/]\n[dim]{v.rule_id}[/]"
        if v.code_snippet:
            body += f"\n\n{v.code_snippet}"

        console.print(Panel(body, title=title, border_style=border_for.get(v.severity, "white")))

        # Fix rendered separately as markdown so ```cpp blocks get syntax highlighted
        if v.fix_suggestion:
            console.print("[green]↪ Suggested Fix:[/]")
            console.print(Markdown(v.fix_suggestion))
            console.print()  # blank line

    _print_summary(violations)


def _render_json(violations: list[Violation], output_path: Path | None) -> None:
    data = [v.model_dump(mode="json") for v in violations]
    text = json.dumps(data, indent=2, default=str)

    if output_path:
        output_path.write_text(text, encoding="utf-8")
        err_console.print(f"[green]✓[/] Wrote JSON: {output_path}")
    else:
        console.print(text)


def _render_html(
    violations: list[Violation],
    output_path: Path | None,
    source_file: Path,
) -> None:
    target = output_path or Path("report.html")
    render_html(violations, target, title=f"safecpp-reviewer — {source_file.name}")
    err_console.print(f"[green]✓[/] Wrote HTML: {target}")


def _print_summary(violations: list[Violation]) -> None:
    from collections import Counter

    counts = Counter(v.severity for v in violations)
    parts = [
        f"[red]{counts.get('error', 0)} errors[/]",
        f"[yellow]{counts.get('warning', 0)} warnings[/]",
        f"[cyan]{counts.get('style', 0)} style[/]",
        f"[dim]{counts.get('note', 0)} notes[/]",
    ]
    console.print(f"\n[bold]Summary:[/] {' · '.join(parts)} ({len(violations)} total)")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def review(
    source: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="C++ source file to analyze.",
        ),
    ],
    fmt: Annotated[
        list[OutputFormat] | None,
        typer.Option(
            "--format",
            "-f",
            help="Output format(s). Repeat for multiple.",
            case_sensitive=False,
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Output file path (used for json/html). Defaults to stdout for json, report.html for html.",
        ),
    ] = None,
    no_llm: Annotated[
        bool,
        typer.Option("--no-llm", help="Skip LLM-based fix suggestions (faster)."),
    ] = False,
    server_url: Annotated[
        str,
        typer.Option("--server", help="llama.cpp server URL."),
    ] = "http://127.0.0.1:8080",
    checks: Annotated[
        str,
        typer.Option(
            "--checks",
            help="clang-tidy check glob.",
        ),
    ] = "cppcoreguidelines-*,modernize-*,readability-*,bugprone-*",
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable info logging.")] = False,
) -> None:
    """Run static analysis + (optional) LLM review on a C++ file."""
    if verbose:
        logging.getLogger().setLevel(logging.INFO)
    if fmt is None:
        fmt = [OutputFormat.terminal]
    # ---- 1. Set up reviewer (or skip)
    reviewer: ViolationReviewer | None = None
    if not no_llm:
        client = LlamaCppClient(base_url=server_url)
        if not client.health_check():
            err_console.print(
                f"[yellow]⚠[/] llama.cpp server unreachable at {server_url} — "
                f"continuing without LLM review."
            )
        else:
            reviewer = ViolationReviewer(client)
            err_console.print(f"[dim]Using LLM at {server_url}[/]")

    # ---- 2. Run analysis
    err_console.print(f"[dim]Analyzing {source}...[/]")
    violations = run_all(
        source,
        clang_tidy_checks=checks,
        reviewer=reviewer,
    )

    # ---- 3. Dispatch to renderers
    formats = set(fmt)  # dedupe if user passed -f html -f html
    for f in formats:
        if f == OutputFormat.terminal:
            _render_terminal(violations)
        elif f == OutputFormat.json:
            _render_json(violations, output)
        elif f == OutputFormat.html:
            _render_html(violations, output, source)

    # ---- 4. Exit code: nonzero if errors found (good for CI)
    error_count = sum(1 for v in violations if v.severity == "error")
    if error_count > 0:
        raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the safecpp-reviewer version."""
    from importlib.metadata import version as pkg_version

    console.print(f"safecpp-reviewer {pkg_version('safecpp-reviewer')}")


if __name__ == "__main__":
    app()

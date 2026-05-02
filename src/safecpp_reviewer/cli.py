"""Command-line interface for safecpp-reviewer.

Two execution modes:

* **Legacy** (default) — calls :func:`safecpp_reviewer.analyzer.run_all`.
* **Graph** (``--use-graph``) — runs the LangGraph pipeline.
Two execution modes:

* **Legacy** (default) — calls :func:`safecpp_reviewer.analyzer.run_all`.
* **Graph** (``--use-graph``) — runs the LangGraph pipeline.

Use ``--compare`` to run both modes and diff the results.
Use ``--compare`` to run both modes and diff the results.
"""

from __future__ import annotations

import enum
import json
import logging
import time
import typing
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from safecpp_reviewer.agent.graph import build_graph, initial_state
from safecpp_reviewer.agent.reviewer import ViolationReviewer
from safecpp_reviewer.agent.verifier import FixVerifier
from safecpp_reviewer.analyzer import run_all
from safecpp_reviewer.analyzer.clang_tidy import ClangTidyRunner
from safecpp_reviewer.analyzer.models import Violation
from safecpp_reviewer.llm.client import LlamaCppClient
from safecpp_reviewer.report import render_html
from safecpp_reviewer.report_index import ReportEntry, render_index

app = typer.Typer(
    name="safecpp",
    help="Agentic LLM-powered C++ safety reviewer (MISRA / AUTOSAR / cppcoreguidelines).",
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


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

    border_for: typing.Final = {"error": "red", "warning": "yellow", "style": "cyan", "note": "dim"}

    for v in violations:
        col = f":{v.column}" if v.column else ""
        title = f"[{v.severity.upper()}] {v.file}:{v.line}{col}"

        body = f"[bold]{v.message}[/]\n[dim]{v.rule_id}[/]"
        if v.code_snippet:
            body += f"\n\n{v.code_snippet}"

        console.print(Panel(body, title=title, border_style=border_for.get(v.severity, "white")))

        if v.fix_suggestion:
            console.print("[green]↪ Suggested Fix:[/]")
            console.print(Markdown(v.fix_suggestion))
            console.print()

    _print_summary(violations)


def _render_json(violations: list[Violation], output_path: Path | None) -> None:
    data: typing.Final = [v.model_dump(mode="json") for v in violations]
    text: typing.Final = json.dumps(data, indent=2, default=str)

    if output_path:
        output_path.write_text(text, encoding="utf-8")
        err_console.print(f"[green]✓[/] Wrote JSON: {output_path}")
    else:
        console.print(text)


def _render_html_output(
    violations: list[Violation],
    output_path: Path | None,
    source_file: Path,
) -> None:
    target: typing.Final = output_path or Path("report.html")
    render_html(violations, target, title=f"safecpp-reviewer — {source_file.name}")
    err_console.print(f"[green]✓[/] Wrote HTML: {target}")


def _print_summary(violations: list[Violation]) -> None:
    from collections import Counter

    counts: typing.Final = Counter(v.severity for v in violations)
    parts: typing.Final = [
        f"[red]{counts.get('error', 0)} errors[/]",
        f"[yellow]{counts.get('warning', 0)} warnings[/]",
        f"[cyan]{counts.get('style', 0)} style[/]",
        f"[dim]{counts.get('note', 0)} notes[/]",
    ]
    console.print(f"\n[bold]Summary:[/] {' · '.join(parts)} ({len(violations)} total)")


# ---------------------------------------------------------------------------
# Pipeline runners
# ---------------------------------------------------------------------------


def _run_legacy(
    source: Path,
    reviewer: ViolationReviewer | None,
    checks: str,
) -> tuple[list[Violation], float]:
    start: typing.Final = time.perf_counter()
    violations: typing.Final = run_all(source, clang_tidy_checks=checks, reviewer=reviewer)
    elapsed: typing.Final = time.perf_counter() - start
    return violations, elapsed


def _run_graph(
    source: Path,
    reviewer: ViolationReviewer | None,
    checks: str,
    verifier: FixVerifier | None = None,  # NEW
) -> tuple[list[Violation], float]:
    graph = build_graph(reviewer=reviewer, verifier=verifier, clang_tidy_checks=checks)
    start = time.perf_counter()
    final_state = graph.invoke(initial_state(source))
    elapsed = time.perf_counter() - start
    return final_state["violations"], elapsed


# ---------------------------------------------------------------------------
# Comparison output
# ---------------------------------------------------------------------------


def _print_comparison(
    legacy: list[Violation],
    graph: list[Violation],
    legacy_secs: float,
    graph_secs: float,
) -> None:
    table: typing.Final = Table(title="Pipeline comparison: legacy vs. graph")
    table.add_column("Metric", style="cyan")
    table.add_column("Legacy", justify="right")
    table.add_column("Graph", justify="right")
    table.add_column("Δ", justify="right")

    table.add_row(
        "Violations",
        str(len(legacy)),
        str(len(graph)),
        f"{len(graph) - len(legacy):+d}",
    )
    fixed_legacy: typing.Final = sum(1 for v in legacy if v.fix_suggestion)
    fixed_graph: typing.Final = sum(1 for v in graph if v.fix_suggestion)
    table.add_row(
        "With LLM fix",
        str(fixed_legacy),
        str(fixed_graph),
        f"{fixed_graph - fixed_legacy:+d}",
    )
    table.add_row(
        "Time (s)",
        f"{legacy_secs:.2f}",
        f"{graph_secs:.2f}",
        f"{graph_secs - legacy_secs:+.2f}",
    )

    console.print(table)

    # Diff which violations differ between runs
    legacy_keys: typing.Final = {(v.tool, str(v.file), v.line, v.rule_id) for v in legacy}
    graph_keys: typing.Final = {(v.tool, str(v.file), v.line, v.rule_id) for v in graph}

    only_legacy: typing.Final = legacy_keys - graph_keys
    only_graph: typing.Final = graph_keys - legacy_keys

    if only_legacy or only_graph:
        console.print("\n[yellow]⚠ Violation set differs between pipelines:[/]")
        for k in only_legacy:
            console.print(f"  [red]- only in legacy:[/] {k}")
        for k in only_graph:
            console.print(f"  [green]+ only in graph:[/]  {k}")
    else:
        console.print("\n[green]✓ Violation sets match.[/]")


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
        typer.Option("--output", "-o", help="Output file path (json/html)."),
    ] = None,
    no_llm: Annotated[
        bool, typer.Option("--no-llm", help="Skip LLM-based fix suggestions.")
    ] = False,
    server_url: Annotated[
        str, typer.Option("--server", help="llama.cpp server URL.")
    ] = "http://127.0.0.1:8080",
    checks: Annotated[
        str, typer.Option("--checks", help="clang-tidy check glob.")
    ] = "cppcoreguidelines-*,modernize-*,readability-*,bugprone-*",
    use_graph: Annotated[
        bool,
        typer.Option(
            "--use-graph",
            help="Run the LangGraph pipeline instead of the legacy run_all.",
        ),
    ] = False,
    verify: Annotated[
        bool,
        typer.Option(
            "--verify",
            help="Re-run clang-tidy on each LLM fix to verify it resolves the violation. "
            "Slower but produces a measurable success metric.",
        ),
    ] = False,
    compare: Annotated[
        bool,
        typer.Option(
            "--compare",
            help="Run both legacy and graph pipelines and diff results.",
        ),
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable info logging.")] = False,
) -> None:
    """Run static analysis + (optional) LLM review on a C++ file."""
    if verbose:
        logging.getLogger().setLevel(logging.INFO)

    verifier: FixVerifier | None = None
    if verify and not no_llm:
        verifier = FixVerifier(clang_tidy=ClangTidyRunner(checks=checks))
        err_console.print("[dim]Verification enabled — fixes will be re-checked.[/]")
        # Verification only makes sense with the graph pipeline
        if not use_graph:
            err_console.print(
                "[yellow]⚠[/] --verify requires --use-graph; enabling graph pipeline."
            )
            use_graph = True

    if fmt is None:
        fmt = [OutputFormat.terminal]

    # ---- Set up reviewer (or skip)
    reviewer: ViolationReviewer | None = None
    if not no_llm:
        client: typing.Final = LlamaCppClient(base_url=server_url)
        if not client.health_check():
            err_console.print(
                f"[yellow]⚠[/] llama.cpp server unreachable at {server_url} — "
                f"continuing without LLM review."
            )
        else:
            reviewer = ViolationReviewer(client)
            err_console.print(f"[dim]Using LLM at {server_url}[/]")

    # ---- Run pipeline(s)
    err_console.print(f"[dim]Analyzing {source}...[/]")

    if compare:
        legacy_violations, legacy_t = _run_legacy(source, reviewer, checks)
        graph_violations, graph_t = _run_graph(source, reviewer, checks, verifier)
        _print_comparison(legacy_violations, graph_violations, legacy_t, graph_t)
        violations = graph_violations  # default to graph output for rendering
    elif use_graph:
        violations, elapsed = _run_graph(source, reviewer, checks, verifier)
        err_console.print(f"[dim]Graph pipeline: {elapsed:.2f}s[/]")
    else:
        violations, elapsed = _run_legacy(source, reviewer, checks)
        err_console.print(f"[dim]Legacy pipeline: {elapsed:.2f}s[/]")

    # ---- Dispatch to renderers
    formats: typing.Final = set(fmt)
    for f in formats:
        if f == OutputFormat.terminal:
            _render_terminal(violations)
        elif f == OutputFormat.json:
            _render_json(violations, output)
        elif f == OutputFormat.html:
            _render_html_output(violations, output, source)

    # ---- Exit code
    error_count: typing.Final = sum(1 for v in violations if v.severity == "error")
    if error_count > 0:
        raise typer.Exit(code=1)

    if verifier is not None:
        verified = sum(1 for v in violations if v.fix_suggestion)
        total_with_attempted_fix = sum(
            1
            for v in violations
            if v.severity in ("error", "warning")  # rough proxy
        )
        if total_with_attempted_fix > 0:
            pct = 100 * verified / total_with_attempted_fix
            console.print(
                f"\n[bold]Verification:[/] {verified}/{total_with_attempted_fix} "
                f"fixes resolved their violation ([green]{pct:.0f}%[/])"
            )


@app.command()
def version() -> None:
    """Print the safecpp-reviewer version."""
    from importlib.metadata import version as pkg_version

    console.print(f"safecpp-reviewer {pkg_version('safecpp-reviewer')}")


@app.command()
def batch(
    sources: Annotated[
        list[Path],
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="C++ source files to analyze.",
        ),
    ],
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Directory for HTML reports."),
    ] = Path("reports"),
    no_llm: Annotated[
        bool, typer.Option("--no-llm", help="Skip LLM-based fix suggestions.")
    ] = False,
    server_url: Annotated[
        str, typer.Option("--server", help="llama.cpp server URL.")
    ] = "http://127.0.0.1:8080",
    checks: Annotated[
        str, typer.Option("--checks", help="clang-tidy check glob.")
    ] = "cppcoreguidelines-*,modernize-*,readability-*,bugprone-*",
    use_graph: Annotated[
        bool, typer.Option("--use-graph", help="Use the LangGraph pipeline.")
    ] = False,
    verify: Annotated[
        bool,
        typer.Option(
            "--verify",
            help="Re-run clang-tidy on each LLM fix to verify it resolves the violation. "
            "Slower but produces a measurable success metric.",
        ),
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Analyze multiple files and generate an indexed HTML report."""

    if verbose:
        logging.getLogger().setLevel(logging.INFO)

    output_dir.mkdir(parents=True, exist_ok=True)

    verifier: FixVerifier | None = None
    if verify and not no_llm:
        verifier = FixVerifier(clang_tidy=ClangTidyRunner(checks=checks))
        err_console.print("[dim]Verification enabled — fixes will be re-checked.[/]")
        # Verification only makes sense with the graph pipeline
        if not use_graph:
            err_console.print(
                "[yellow]⚠[/] --verify requires --use-graph; enabling graph pipeline."
            )
            use_graph = True

    # ---- Set up reviewer (shared across all files)
    reviewer: ViolationReviewer | None = None
    if not no_llm:
        client: typing.Final = LlamaCppClient(base_url=server_url)
        if client.health_check():
            reviewer = ViolationReviewer(client)
            err_console.print(f"[dim]Using LLM at {server_url}[/]")
        else:
            err_console.print(
                f"[yellow]⚠[/] LLM unreachable at {server_url} — running without LLM."
            )

    runner: typing.Final = _run_graph if use_graph else _run_legacy
    entries: typing.Final[list[ReportEntry]] = []
    total_errors = 0

    for src in sources:
        err_console.print(f"[dim]Analyzing {src}...[/]")
        try:
            violations, elapsed = runner(src, reviewer, checks, verifier)
        except Exception as exc:
            err_console.print(f"[red]✗[/] Failed on {src}: {exc}")
            continue

        report_file = output_dir / f"{_safe_name(src)}.html"
        render_html(
            violations,
            report_file,
            title=f"{src.name}",
            back_link="index.html",
        )

        entries.append(ReportEntry(source=src, report_path=report_file, violations=violations))
        total_errors += sum(1 for v in violations if v.severity == "error")

        err_console.print(f"  → {len(violations)} violation(s), {elapsed:.2f}s → {report_file}")

    index_path: typing.Final = output_dir / "index.html"
    render_index(entries, index_path, title="safecpp-reviewer — Batch Report")
    console.print(
        f"\n[bold green]✓[/] Generated index: [cyan]{index_path}[/] "
        f"({len(entries)} files, {sum(e.total for e in entries)} violations)"
    )

    if total_errors > 0:
        raise typer.Exit(code=1)

    if verifier is not None:
        verified = sum(1 for v in violations if v.fix_suggestion)
        total_with_attempted_fix = sum(
            1
            for v in violations
            if v.severity in ("error", "warning")  # rough proxy
        )
        if total_with_attempted_fix > 0:
            pct = 100 * verified / total_with_attempted_fix
            console.print(
                f"\n[bold]Verification:[/] {verified}/{total_with_attempted_fix} "
                f"fixes resolved their violation ([green]{pct:.0f}%[/])"
            )


def _safe_name(path: Path) -> str:
    """Build a filesystem-safe report name from a source path."""
    return str(path).replace("/", "_").replace("\\", "_").lstrip("_")


if __name__ == "__main__":
    app()

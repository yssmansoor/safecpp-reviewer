"""Command-line interface for safecpp-reviewer.

Two execution modes:

* **Legacy** (default) — calls :func:`safecpp_reviewer.analyzer.run_all`.
* **Graph** (``--use-graph``) — runs the LangGraph pipeline.

Use ``--compare`` to run both modes and diff the results.
Use ``--verify`` (with ``--use-graph``) to re-run clang-tidy on each LLM fix.
Use ``--changed-lines`` + ``-f github`` to produce PR review comments.
"""

# At the top of cli.py, remove this line:
from __future__ import annotations

import enum
import json
import logging
import time
from collections import Counter
from pathlib import Path

# And make sure this is in your imports:
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
from safecpp_reviewer.analyzer.changed_lines import (
    filter_to_changed_lines,
    load_changed_lines,
)
from safecpp_reviewer.analyzer.clang_tidy import ClangTidyRunner
from safecpp_reviewer.analyzer.models import Violation
from safecpp_reviewer.eval import EvalHarness, EvalSummary, load_cases
from safecpp_reviewer.llm.client import LlamaCppClient
from safecpp_reviewer.report import render_html
from safecpp_reviewer.report_github import render_github
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
    github = "github"


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
    data = [v.model_dump(mode="json") for v in violations]
    text = json.dumps(data, indent=2, default=str)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
        err_console.print(f"[green]✓[/] Wrote JSON: {output_path}")
    else:
        console.print(text)


def _render_html_output(
    violations: list[Violation],
    output_path: Path | None,
    source_file: Path,
) -> None:
    target = output_path or Path("report.html")
    render_html(violations, target, title=f"safecpp-reviewer — {source_file.name}")
    err_console.print(f"[green]✓[/] Wrote HTML: {target}")


def _render_github_output(
    violations: list[Violation],
    output_path: Path | None,
    repo_root: Path | None,
) -> None:
    target = output_path or Path("comments.json")
    render_github(violations, output_path=target, repo_root=repo_root)
    err_console.print(f"[green]✓[/] Wrote GitHub payload: {target}")


def _print_summary(violations: list[Violation]) -> None:
    counts = Counter(v.severity for v in violations)
    parts = [
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
    start = time.perf_counter()
    violations = run_all(source, clang_tidy_checks=checks, reviewer=reviewer)
    elapsed = time.perf_counter() - start
    return violations, elapsed


def _run_graph(
    source: Path,
    reviewer: ViolationReviewer | None,
    checks: str,
    verifier: FixVerifier | None = None,
) -> tuple[list[Violation], float]:
    graph = build_graph(
        reviewer=reviewer,
        verifier=verifier,
        clang_tidy_checks=checks,
    )
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
    table = Table(title="Pipeline comparison: legacy vs. graph")
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
    fixed_legacy = sum(1 for v in legacy if v.fix_suggestion)
    fixed_graph = sum(1 for v in graph if v.fix_suggestion)
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

    legacy_keys = {(v.tool, str(v.file), v.line, v.rule_id) for v in legacy}
    graph_keys = {(v.tool, str(v.file), v.line, v.rule_id) for v in graph}

    only_legacy = legacy_keys - graph_keys
    only_graph = graph_keys - legacy_keys

    if only_legacy or only_graph:
        console.print("\n[yellow]⚠ Violation set differs between pipelines:[/]")
        for k in only_legacy:
            console.print(f"  [red]- only in legacy:[/] {k}")
        for k in only_graph:
            console.print(f"  [green]+ only in graph:[/]  {k}")
    else:
        console.print("\n[green]✓ Violation sets match.[/]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_reviewer(
    no_llm: bool, server_url: str, model: str = "qwen2.5-coder-7b"
) -> ViolationReviewer | None:
    """Construct a reviewer if the LLM server is reachable, else None."""
    if no_llm:
        return None
    client = LlamaCppClient(base_url=server_url, model=model)
    if not client.health_check():
        err_console.print(
            f"[yellow]⚠[/] llama.cpp server unreachable at {server_url} — "
            f"continuing without LLM review."
        )
        return None
    err_console.print(f"[dim]Using LLM at {server_url}[/]")
    return ViolationReviewer(client)


def _build_verifier(verify: bool, no_llm: bool, checks: str) -> FixVerifier | None:
    """Construct a verifier when verification is requested with LLM enabled."""
    if not verify or no_llm:
        return None
    return FixVerifier(clang_tidy=ClangTidyRunner(checks=checks))


def _safe_name(path: Path) -> str:
    """Filesystem-safe report name from a source path."""
    return str(path).replace("/", "_").replace("\\", "_").lstrip("_")


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
        typer.Option("--output", "-o", help="Output file path (json/html/github)."),
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
    compare: Annotated[
        bool,
        typer.Option(
            "--compare",
            help="Run both legacy and graph pipelines and diff results.",
        ),
    ] = False,
    verify: Annotated[
        bool,
        typer.Option(
            "--verify",
            help="Re-run clang-tidy on each LLM fix to verify resolution. Implies --use-graph.",
        ),
    ] = False,
    changed_lines: Annotated[
        Path | None,
        typer.Option(
            "--changed-lines",
            help="JSON file mapping {filename: [lines]} — only flag violations "
            "on these lines. Used by the GitHub Action for PR diffs.",
            exists=True,
            file_okay=True,
            dir_okay=False,
        ),
    ] = None,
    repo_root: Annotated[
        Path | None,
        typer.Option(
            "--repo-root",
            help="Repository root for resolving relative paths (github format).",
        ),
    ] = None,
    model: Annotated[
        str,
        typer.Option("--model", help="Model identifier sent to the LLM server."),
    ] = "qwen2.5-coder-7b",
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable info logging.")] = False,
) -> None:
    """Run static analysis + (optional) LLM review on a C++ file."""
    if verbose:
        logging.getLogger().setLevel(logging.INFO)

    if fmt is None:
        fmt = [OutputFormat.terminal]

    # ---- Reviewer & verifier
    reviewer = _build_reviewer(no_llm, server_url, model=model)
    verifier = _build_verifier(verify, no_llm, checks)

    if verifier is not None:
        if not use_graph:
            err_console.print(
                "[yellow]⚠[/] --verify requires --use-graph; enabling graph pipeline."
            )
            use_graph = True
        err_console.print("[dim]Verification enabled — fixes will be re-checked.[/]")

    # ---- Run pipeline(s)
    err_console.print(f"[dim]Analyzing {source}...[/]")

    if compare:
        legacy_violations, legacy_t = _run_legacy(source, reviewer, checks)
        graph_violations, graph_t = _run_graph(source, reviewer, checks, verifier)
        _print_comparison(legacy_violations, graph_violations, legacy_t, graph_t)
        violations = graph_violations
    elif use_graph:
        violations, elapsed = _run_graph(source, reviewer, checks, verifier)
        err_console.print(f"[dim]Graph pipeline: {elapsed:.2f}s[/]")
    else:
        violations, elapsed = _run_legacy(source, reviewer, checks)
        err_console.print(f"[dim]Legacy pipeline: {elapsed:.2f}s[/]")

    # ---- Filter to changed lines (PR mode)
    if changed_lines is not None:
        changed = load_changed_lines(changed_lines)
        before = len(violations)
        violations = filter_to_changed_lines(violations, changed)
        err_console.print(
            f"[dim]Changed-lines filter: {before} → {len(violations)} violation(s)[/]"
        )

    # ---- Render
    formats = set(fmt)
    for f in formats:
        if f == OutputFormat.terminal:
            _render_terminal(violations)
        elif f == OutputFormat.json:
            _render_json(violations, output)
        elif f == OutputFormat.html:
            _render_html_output(violations, output, source)
        elif f == OutputFormat.github:
            _render_github_output(violations, output, repo_root)

    # ---- Verification summary
    if verifier is not None and violations:
        verified = sum(1 for v in violations if v.fix_suggestion)
        attempted = sum(1 for v in violations if v.severity in ("error", "warning"))
        if attempted > 0:
            pct = 100 * verified / attempted
            console.print(
                f"\n[bold]Verification:[/] {verified}/{attempted} fixes resolved "
                f"their violation ([green]{pct:.0f}%[/])"
            )

    # ---- Exit code (CI-friendly)
    error_count = sum(1 for v in violations if v.severity == "error")
    if error_count > 0:
        raise typer.Exit(code=1)


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
            help="Re-run clang-tidy on each LLM fix. Implies --use-graph.",
        ),
    ] = False,
    model: Annotated[
        str,
        typer.Option("--model", help="Model identifier sent to the LLM server."),
    ] = "qwen2.5-coder-7b",
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Analyze multiple files and generate an indexed HTML report."""
    if verbose:
        logging.getLogger().setLevel(logging.INFO)

    output_dir.mkdir(parents=True, exist_ok=True)

    # ---- Set up reviewer & verifier (shared across all files)
    reviewer = _build_reviewer(no_llm, server_url, model=model)
    verifier = _build_verifier(verify, no_llm, checks)

    if verifier is not None and not use_graph:
        err_console.print("[yellow]⚠[/] --verify requires --use-graph; enabling graph pipeline.")
        use_graph = True

    entries: list[ReportEntry] = []
    total_errors = 0

    for src in sources:
        err_console.print(f"[dim]Analyzing {src}...[/]")
        try:
            if use_graph:
                violations, elapsed = _run_graph(src, reviewer, checks, verifier)
            else:
                violations, elapsed = _run_legacy(src, reviewer, checks)
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

    index_path = output_dir / "index.html"
    render_index(entries, index_path, title="safecpp-reviewer — Batch Report")
    console.print(
        f"\n[bold green]✓[/] Generated index: [cyan]{index_path}[/] "
        f"({len(entries)} files, {sum(e.total for e in entries)} violations)"
    )

    if total_errors > 0:
        raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the safecpp-reviewer version."""
    from importlib.metadata import version as pkg_version

    console.print(f"safecpp-reviewer {pkg_version('safecpp-reviewer')}")


@app.command(name="eval")
def eval_command(
    server_url: Annotated[
        str,
        typer.Option("--server", help="OpenAI-compatible LLM server URL."),
    ] = "http://127.0.0.1:8080",
    model: Annotated[
        str,
        typer.Option(
            "--model",
            help="Model identifier for the report (descriptive only).",
        ),
    ] = "qwen2.5-coder-7b",
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Where to write the JSON summary. Stdout if omitted.",
        ),
    ] = None,
    cases_dir: Annotated[
        Path | None,
        typer.Option(
            "--cases",
            help="Directory of eval YAML files (default: bundled).",
            exists=True,
            file_okay=False,
            dir_okay=True,
        ),
    ] = None,
    rule_filter: Annotated[
        str | None,
        typer.Option(
            "--rule",
            help="Only run cases whose rule_id contains this substring.",
        ),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Run the LLM fix-quality eval suite and emit a summary report."""
    if verbose:
        logging.getLogger().setLevel(logging.INFO)

    # ---- Build reviewer (eval requires LLM)
    client = LlamaCppClient(base_url=server_url)
    if not client.health_check():
        err_console.print(
            f"[red]✗[/] LLM server unreachable at {server_url}. Eval requires a running model."
        )
        raise typer.Exit(code=2)

    reviewer = ViolationReviewer(client)

    # ---- Load cases
    cases = load_cases(cases_dir)
    if rule_filter:
        cases = [c for c in cases if rule_filter in c.rule_id]
    if not cases:
        err_console.print("[red]✗[/] No eval cases found.")
        raise typer.Exit(code=2)

    err_console.print(f"[dim]Running {len(cases)} eval case(s) against {model} at {server_url}[/]")

    # ---- Run harness
    harness = EvalHarness(reviewer=reviewer)
    summary = harness.run(cases, model_name=model)

    # ---- Print results
    _print_eval_summary(summary)

    # ---- Persist
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
        err_console.print(f"[green]✓[/] Wrote eval summary: {output}")


def _print_eval_summary(summary: EvalSummary) -> None:
    """Render an EvalSummary as a rich table."""
    overall = Table(title=f"Eval results — {summary.model}")
    overall.add_column("Metric", style="cyan")
    overall.add_column("Value", justify="right")
    overall.add_row("Total cases", str(summary.total_cases))
    overall.add_row(
        "Verified (pass)",
        f"{summary.verified_count}/{summary.total_cases} ({summary.pass_rate:.0%})",
    )
    overall.add_row("Mean similarity", f"{summary.mean_similarity:.2f}")
    overall.add_row("Keyword matches", f"{summary.keyword_match_count}/{summary.total_cases}")
    overall.add_row("Mean score", f"{summary.mean_score:.3f} / 1.500")
    console.print(overall)

    if summary.by_rule:
        per_rule = Table(title="Per-rule breakdown")
        per_rule.add_column("Rule", style="cyan")
        per_rule.add_column("Cases", justify="right")
        per_rule.add_column("Verified", justify="right")
        per_rule.add_column("Pass rate", justify="right")
        per_rule.add_column("Mean score", justify="right")

        for rule_id, stats in sorted(summary.by_rule.items(), key=lambda kv: -kv[1].mean_score):
            per_rule.add_row(
                rule_id,
                str(stats.total),
                str(stats.verified),
                f"{stats.pass_rate:.0%}",
                f"{stats.mean_score:.3f}",
            )
        console.print(per_rule)


# ============================================================
# Add eval-compare command for ablation tables across runs
# ============================================================


@app.command(name="eval-compare")
def eval_compare(
    summaries: Annotated[
        list[Path],
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Two or more eval summary JSON files.",
        ),
    ],
) -> None:
    """Print an ablation table comparing multiple eval runs."""
    if len(summaries) < 2:
        err_console.print("[red]✗[/] Need at least two summaries to compare.")
        raise typer.Exit(code=2)

    loaded: list[EvalSummary] = []
    for p in summaries:
        loaded.append(EvalSummary.model_validate_json(p.read_text()))

    table = Table(title="Eval comparison")
    table.add_column("Metric", style="cyan")
    for s in loaded:
        table.add_column(s.model, justify="right")

    rows = [
        ("Cases", lambda s: str(s.total_cases)),
        ("Pass rate", lambda s: f"{s.pass_rate:.0%}"),
        ("Mean score", lambda s: f"{s.mean_score:.3f}"),
        ("Keyword match", lambda s: f"{s.keyword_match_count}/{s.total_cases}"),
        ("Mean similarity", lambda s: f"{s.mean_similarity:.2f}"),
    ]
    for label, getter in rows:
        table.add_row(label, *(getter(s) for s in loaded))

    console.print(table)


if __name__ == "__main__":
    app()

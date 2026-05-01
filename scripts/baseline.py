from pathlib import Path

from rich.console import Console

from safecpp_reviewer.agent.reviewer import ViolationReviewer
from safecpp_reviewer.analyzer import run_all
from safecpp_reviewer.llm.client import LlamaCppClient
from safecpp_reviewer.report import render_html

console = Console()

# Static analysis only (fast, no LLM):
# violations = run_all(Path("tests/fixtures/sample_violations.cpp"))

# With LLM review (slow, needs llama-server running):
client = LlamaCppClient()
reviewer = ViolationReviewer(client)
violations = run_all(
    Path("tests/fixtures/sample_violations.cpp"),
    reviewer=reviewer,
)

render_html(violations, Path("report.html"))
print(f"Wrote report.html with {len(violations)} violations")

for v in violations:
    console.print(v.short(color=True))
    console.print()

"""LangGraph pipeline construction.

Compiles the review pipeline into a graph that can be invoked with an
initial :class:`ReviewerState` dict.  Heavy dependencies (the LLM-backed
:class:`ViolationReviewer`) are injected via :func:`build_graph` so the
same graph can be tested with mocks or run against a real model.
"""

from __future__ import annotations

import typing
from pathlib import Path

from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from safecpp_reviewer.agent.nodes import (
    chunk_node,
    make_analyze_node,
    make_retry_node,
    make_review_node,
    should_retry,
)
from safecpp_reviewer.agent.reviewer import ViolationReviewer
from safecpp_reviewer.agent.state import ReviewerState


def build_graph(
    reviewer: ViolationReviewer | None = None,
    clang_tidy_checks: str = "cppcoreguidelines-*,modernize-*,readability-*,bugprone-*",
    extra_compiler_args: list[str] | None = None,
    max_batch_size: int = 5,
    max_retries: int = 2,
) -> CompiledStateGraph[ReviewerState, None, ReviewerState, ReviewerState]:
    """Compile the review pipeline.

    Args:
        reviewer: LLM-backed reviewer, or ``None`` to skip the review step.
        clang_tidy_checks: Glob forwarded to the analyze node.
        extra_compiler_args: Compiler flags forwarded to clang-tidy.
        max_batch_size: Max violations per batched chunk review.
        max_retries: How many times to retry a failed individual review.

    Returns:
        A compiled graph ready to ``.invoke(initial_state)``.
    """
    g: typing.Final[StateGraph[ReviewerState]] = StateGraph[ReviewerState](ReviewerState)

    g.add_node(
        "analyze",
        RunnableLambda(
            make_analyze_node(
                clang_tidy_checks=clang_tidy_checks,
                extra_compiler_args=extra_compiler_args,
            )
        ),
    )
    g.add_node("chunk", RunnableLambda(chunk_node))
    g.add_node("review", RunnableLambda(make_review_node(reviewer, max_batch_size=max_batch_size)))
    g.add_node("retry", RunnableLambda(make_retry_node(reviewer, max_retries=max_retries)))

    g.set_entry_point("analyze")
    g.add_edge("analyze", "chunk")
    g.add_edge("chunk", "review")
    g.add_conditional_edges("review", should_retry, {"retry": "retry", "done": END})
    g.add_conditional_edges("retry", should_retry, {"retry": "retry", "done": END})

    compiled_graph: typing.Final[
        CompiledStateGraph[ReviewerState, None, ReviewerState, ReviewerState]
    ] = g.compile()
    return compiled_graph


def initial_state(source_file: Path) -> ReviewerState:
    """Build a fresh state envelope for a single source file."""

    return ReviewerState(
        source_file=source_file,
        violations=[],
        chunks=[],
        failed_reviews=[],
        retry_count=0,
        output_formats=[],
        output_path=None,
    )

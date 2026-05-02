"""LangGraph nodes for the review pipeline.

Each node is a pure function ``(state) -> partial_state``.  Nodes are kept
small and stateless — heavy dependencies (LLM client, rule store) are
injected via closures in :func:`safecpp_reviewer.agent.graph.build_graph`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from safecpp_reviewer.agent.reviewer import ViolationReviewer
from safecpp_reviewer.agent.state import ReviewerState
from safecpp_reviewer.analyzer.clang_tidy import ClangTidyRunner
from safecpp_reviewer.analyzer.cppcheck import CppcheckRunner
from safecpp_reviewer.analyzer.models import Violation
from safecpp_reviewer.analyzer.snippet import extract_snippet
from safecpp_reviewer.chunker import parse_chunks
from safecpp_reviewer.chunker.models import Chunk

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node: static analysis
# ---------------------------------------------------------------------------


def make_analyze_node(
    clang_tidy_checks: str = "cppcoreguidelines-*,modernize-*,readability-*,bugprone-*",
    cppcheck_enable: str = "all",
    extra_compiler_args: list[str] | None = None,
) -> Callable[[ReviewerState], dict]:
    """Build the static-analysis node with its config bound by closure."""
    compiler_args = extra_compiler_args or ["-std=c++17"]

    def analyze_node(state: ReviewerState) -> dict:
        source_file = state["source_file"]
        logger.info("analyze_node: running static analysis on %s", source_file)

        ct_runner = ClangTidyRunner(checks=clang_tidy_checks, extra_args=compiler_args)
        cc_runner = CppcheckRunner(enable=cppcheck_enable)

        violations: list[Violation] = []
        try:
            violations.extend(ct_runner.run(source_file))
        except RuntimeError as exc:
            logger.warning("clang-tidy unavailable: %s", exc)
        try:
            violations.extend(cc_runner.run(source_file))
        except RuntimeError as exc:
            logger.warning("cppcheck unavailable: %s", exc)

        # Deduplicate by (tool, file, line, rule_id)
        seen: set[tuple[str, str, int, str]] = set()
        unique: list[Violation] = []
        for v in violations:
            key = (v.tool, str(v.file), v.line, v.rule_id)
            if key not in seen:
                seen.add(key)
                unique.append(v)

        # Populate code_snippet now so downstream nodes have it
        for v in unique:
            v.code_snippet = extract_snippet(v)

        unique.sort(key=lambda v: (str(v.file), v.line, v.column or 0))
        logger.info("analyze_node: %d unique violation(s)", len(unique))
        return {"violations": unique}

    return analyze_node


# ---------------------------------------------------------------------------
# Node: chunking
# ---------------------------------------------------------------------------


def chunk_node(state: ReviewerState) -> dict:
    """Parse the source file into function/class/namespace chunks.

    Returns an empty list if the chunker fails — downstream nodes treat
    that as "no chunk available" and fall back to per-violation review.
    """
    source_file = state["source_file"]
    try:
        chunks = parse_chunks(source_file)
    except Exception as exc:
        logger.warning("chunk_node: parse failed (%s) — continuing without chunks", exc)
        chunks = []

    logger.info("chunk_node: %d chunk(s)", len(chunks))
    return {"chunks": chunks}


# ---------------------------------------------------------------------------
# Node: review
# ---------------------------------------------------------------------------


def make_review_node(
    reviewer: ViolationReviewer | None,
    max_batch_size: int = 5,
) -> Callable[[ReviewerState], dict]:
    """Build the review node with the reviewer bound by closure.

    If *reviewer* is ``None`` the node is a no-op — useful for ``--no-llm``.
    """

    def review_node(state: ReviewerState) -> dict:
        violations = state["violations"]
        chunks = state["chunks"]

        if reviewer is None:
            logger.info("review_node: no reviewer configured — skipping")
            return {"failed_reviews": []}

        if not violations:
            return {"failed_reviews": []}

        failed: list[Violation] = []

        if not chunks:
            # No chunking available — review each violation individually.
            for v in violations:
                _review_single(reviewer, v, failed)
            return {"violations": violations, "failed_reviews": failed}

        # Group violations by chunk; track those outside any chunk as orphans.
        chunk_groups: dict[int, tuple[Chunk, list[Violation]]] = {}
        orphans: list[Violation] = []

        for v in violations:
            for i, chunk in enumerate(chunks):
                if v.line in chunk:
                    chunk_groups.setdefault(i, (chunk, []))[1].append(v)
                    break
            else:
                orphans.append(v)

        for chunk, vs in chunk_groups.values():
            if len(vs) > max_batch_size:
                # Too many violations for one batched call — fall back individual.
                logger.debug(
                    "review_node: chunk %s has %d violations, exceeding batch size %d",
                    chunk.name,
                    len(vs),
                    max_batch_size,
                )
                for v in vs:
                    _review_single(reviewer, v, failed)
            else:
                try:
                    reviewer.review_chunk(chunk, vs)
                except Exception as exc:
                    logger.warning("review_chunk failed for %s: %s", chunk.name, exc)
                    failed.extend(vs)

        for v in orphans:
            _review_single(reviewer, v, failed)

        logger.info(
            "review_node: %d violation(s) reviewed, %d failed",
            len(violations) - len(failed),
            len(failed),
        )
        return {"violations": violations, "failed_reviews": failed}

    return review_node


def _review_single(
    reviewer: ViolationReviewer,
    violation: Violation,
    failed: list[Violation],
) -> None:
    """Run single-violation review and record failures."""
    try:
        reviewer.review(violation)
        if violation.fix_suggestion is None:
            failed.append(violation)
    except Exception as exc:
        logger.warning("review failed for %s: %s", violation.short(), exc)
        failed.append(violation)


# ---------------------------------------------------------------------------
# Node: retry (optional, wired in via conditional edge)
# ---------------------------------------------------------------------------


def make_retry_node(
    reviewer: ViolationReviewer | None,
    max_retries: int = 2,
) -> Callable[[ReviewerState], dict]:
    """Re-run review on previously-failed violations once, with bumped temp."""

    def retry_node(state: ReviewerState) -> dict:
        failed = state.get("failed_reviews", [])
        retry_count = state.get("retry_count", 0)

        if reviewer is None or not failed or retry_count >= max_retries:
            return {"failed_reviews": failed, "retry_count": retry_count}

        logger.info(
            "retry_node: retrying %d failed review(s), attempt %d/%d",
            len(failed),
            retry_count + 1,
            max_retries,
        )

        still_failed: list[Violation] = []
        for v in failed:
            try:
                reviewer.review(v)
                if v.fix_suggestion is None:
                    still_failed.append(v)
            except Exception:
                still_failed.append(v)

        return {
            "failed_reviews": still_failed,
            "retry_count": retry_count + 1,
        }

    return retry_node


def should_retry(state: ReviewerState) -> str:
    """Conditional edge: retry if there are failures and we haven't capped out."""
    failed = state.get("failed_reviews", [])
    retry_count = state.get("retry_count", 0)
    return "retry" if failed and retry_count < 2 else "done"

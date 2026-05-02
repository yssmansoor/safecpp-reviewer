"""LangGraph nodes for the review pipeline.

Each node is a pure function ``(state) -> partial_state``.  Nodes are kept
small and stateless — heavy dependencies (LLM client, rule store) are
injected via closures in :func:`safecpp_reviewer.agent.graph.build_graph`.
"""

from __future__ import annotations

import logging
import typing
from collections.abc import Callable

from safecpp_reviewer.agent.reviewer import ViolationReviewer
from safecpp_reviewer.agent.state import ReviewerState
from safecpp_reviewer.agent.verifier import FixVerifier
from safecpp_reviewer.analyzer.clang_tidy import ClangTidyRunner
from safecpp_reviewer.analyzer.cppcheck import CppcheckRunner
from safecpp_reviewer.analyzer.filter import filter_real_violations
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
) -> Callable[[ReviewerState], dict[str, list[Violation]]]:
    """Build the static-analysis node with its config bound by closure."""
    compiler_args: typing.Final = extra_compiler_args or ["-std=c++17"]

    def analyze_node(state: ReviewerState) -> dict[str, list[Violation]]:
        source_file: typing.Final = state["source_file"]
        logger.info("analyze_node: running static analysis on %s", source_file)

        ct_runner: typing.Final = ClangTidyRunner(
            checks=clang_tidy_checks, extra_args=compiler_args
        )
        cc_runner: typing.Final = CppcheckRunner(enable=cppcheck_enable)

        violations: typing.Final[list[Violation]] = []
        try:
            violations.extend(ct_runner.run(source_file))
        except RuntimeError as exc:
            logger.warning("clang-tidy unavailable: %s", exc)
        try:
            violations.extend(cc_runner.run(source_file))
        except RuntimeError as exc:
            logger.warning("cppcheck unavailable: %s", exc)

        # Deduplicate by (tool, file, line, rule_id)
        seen: typing.Final[set[tuple[str, str, int, str]]] = set()
        unique: typing.Final[list[Violation]] = []
        for v in violations:
            key = (v.tool, str(v.file), v.line, v.rule_id)
            if key not in seen:
                seen.add(key)
                unique.append(v)

        # NEW: drop meta-diagnostics (clang-diagnostic-error etc.)
        unique = filter_real_violations(unique)

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


def chunk_node(state: ReviewerState) -> dict[str, list[Chunk]]:
    """Parse the source file into function/class/namespace chunks.

    Returns an empty list if the chunker fails — downstream nodes treat
    that as "no chunk available" and fall back to per-violation review.
    """
    source_file: typing.Final = state["source_file"]
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
) -> Callable[[ReviewerState], dict[str, typing.Any]]:
    """Build the review node with the reviewer bound by closure.

    If *reviewer* is ``None`` the node is a no-op — useful for ``--no-llm``.
    """

    def review_node(state: ReviewerState) -> dict[str, typing.Any]:
        violations: typing.Final = state["violations"]
        chunks: typing.Final = state["chunks"]

        if reviewer is None:
            logger.info("review_node: no reviewer configured — skipping")
            return {"failed_reviews": []}

        if not violations:
            return {"failed_reviews": []}

        failed: typing.Final[list[Violation]] = []

        if not chunks:
            # No chunking available — review each violation individually.
            for v in violations:
                _review_single(reviewer, v, failed)
            return {"violations": violations, "failed_reviews": failed}

        # Group violations by chunk; track those outside any chunk as orphans.
        chunk_groups: typing.Final[dict[int, tuple[Chunk, list[Violation]]]] = {}
        orphans: typing.Final[list[Violation]] = []

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
) -> Callable[[ReviewerState], dict[str, typing.Any]]:
    """Re-run review on previously-failed violations once, with bumped temp."""

    def retry_node(state: ReviewerState) -> dict[str, typing.Any]:
        failed: typing.Final = state.get("failed_reviews", [])
        retry_count: typing.Final = state.get("retry_count", 0)

        if reviewer is None or not failed or retry_count >= max_retries:
            return {"failed_reviews": failed, "retry_count": retry_count}

        logger.info(
            "retry_node: retrying %d failed review(s), attempt %d/%d",
            len(failed),
            retry_count + 1,
            max_retries,
        )

        still_failed: typing.Final[list[Violation]] = []
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
    failed: typing.Final = state.get("failed_reviews", [])
    retry_count: typing.Final = state.get("retry_count", 0)
    return "retry" if failed and retry_count < 2 else "done"


def make_verify_node(
    verifier: FixVerifier | None,
) -> Callable[[ReviewerState], dict[str, typing.Any]]:
    """Build the verification node.

    For each violation that has a ``fix_suggestion``, apply the fix and
    re-run clang-tidy.  Violations whose fix doesn't resolve the original
    rule_id are added to ``failed_reviews`` so the retry node can try again.
    """

    def verify_node(state: ReviewerState) -> dict[str, typing.Any]:
        if verifier is None:
            return {"failed_reviews": state.get("failed_reviews", [])}

        violations = state["violations"]
        chunks = state["chunks"]
        existing_failed = list(state.get("failed_reviews", []))

        unresolved: list[Violation] = []
        regression_count = 0
        partial_fix_rejections = 0

        for v in violations:
            if v.fix_suggestion is None:
                continue

            chunk = next((c for c in chunks if v.line in c), None)
            result = verifier.verify(v, chunk=chunk)

            if not result.resolved:
                # Per-violation failures get INFO; the summary below gets WARNING
                logger.info(
                    "verify_node: fix did not resolve %s (error=%s)",
                    v.rule_id,
                    result.error,
                )
                if result.error and "partial fix" in result.error:
                    partial_fix_rejections += 1

                # Drop the unverified fix so the retry node tries again
                v.fix_suggestion = None
                unresolved.append(v)

            if result.regressed:
                regression_count += len(result.new_violations)
                # Per-fix regressions also INFO — the summary tells the story
                logger.info(
                    "verify_node: fix for %s introduced %d new violation(s)",
                    v.rule_id,
                    len(result.new_violations),
                )

        # ---- Single summary line at WARNING level if anything went wrong
        total = sum(1 for v in violations if v.fix_suggestion or v in unresolved)
        if unresolved or regression_count:
            logger.warning(
                "verify_node: %d/%d fix(es) verified, %d unresolved "
                "(%d rejected as partial), %d total regression(s)",
                total - len(unresolved),
                total,
                len(unresolved),
                partial_fix_rejections,
                regression_count,
            )
        else:
            logger.info(
                "verify_node: all %d fix(es) verified clean",
                total,
            )

        all_failed = existing_failed + [v for v in unresolved if v not in existing_failed]
        return {"failed_reviews": all_failed}

    return verify_node

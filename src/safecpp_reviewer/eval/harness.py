"""Eval harness: runs each :class:`EvalCase` through an LLM and scores it.

The harness is deliberately model-agnostic: any :class:`ViolationReviewer`
works, so the same suite can be run against local llama.cpp, OpenAI, or
any other OpenAI-compatible endpoint.
"""

from __future__ import annotations

import logging
import re
import tempfile
import time
from pathlib import Path

from safecpp_reviewer.agent.reviewer import ViolationReviewer
from safecpp_reviewer.agent.verifier import FixVerifier
from safecpp_reviewer.analyzer.clang_tidy import ClangTidyRunner
from safecpp_reviewer.analyzer.models import Violation
from safecpp_reviewer.eval.models import EvalCase, EvalResult, EvalSummary
from safecpp_reviewer.eval.scorer import score_case

logger = logging.getLogger(__name__)

# Re-uses the verifier's fence regex
_FENCE_RE = re.compile(r"```(?:cpp|c\+\+)?\s*\n(.*?)```", re.DOTALL)


class EvalHarness:
    """Drives one full eval pass over a list of cases.

    Args:
        reviewer: The LLM-backed reviewer to evaluate.
        verifier: Used to confirm whether the LLM's fix actually resolves the
            violation.  If omitted the harness still computes similarity and
            keyword scores but ``verified`` is always ``False``.
    """

    def __init__(
        self,
        reviewer: ViolationReviewer,
        verifier: FixVerifier | None = None,
    ) -> None:
        self.reviewer = reviewer
        self.verifier = verifier or FixVerifier(clang_tidy=ClangTidyRunner())

    def run(self, cases: list[EvalCase], model_name: str) -> EvalSummary:
        """Run *cases* through the reviewer and return the summary."""
        results: list[EvalResult] = []
        for i, case in enumerate(cases, 1):
            logger.info("eval [%d/%d]: %s", i, len(cases), case.case_id)
            results.append(self._run_one(case))

        return EvalSummary.from_results(
            results,
            model=model_name,
            server_url=getattr(self.reviewer.client, "base_url", "unknown"),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _run_one(self, case: EvalCase) -> EvalResult:
        start = time.perf_counter()

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "case.cpp"
            src.write_text(case.bad_code, encoding="utf-8")

            violation = Violation(
                file=src,
                line=self._estimate_violation_line(case),
                column=1,
                rule_id=case.rule_id,
                severity=case.severity,  # type: ignore[arg-type]
                message=case.description,
                tool="clang-tidy",
                code_snippet=case.bad_code,
            )

            try:
                self.reviewer.review(violation)
            except Exception as exc:
                return EvalResult(
                    case_id=case.case_id,
                    rule_id=case.rule_id,
                    error=f"reviewer failed: {exc}",
                    elapsed_seconds=time.perf_counter() - start,
                )

            llm_fix_raw = violation.fix_suggestion or ""
            llm_fix = self._extract_code(llm_fix_raw)
            llm_explanation = self._extract_explanation(llm_fix_raw)

            # Verify against the actual case file
            try:
                vresult = self.verifier.verify(violation)
                verified = vresult.resolved
            except Exception as exc:
                logger.warning("verification failed for %s: %s", case.case_id, exc)
                verified = False

        score, similarity, kw_match = score_case(case, llm_fix, llm_explanation, verified)

        return EvalResult(
            case_id=case.case_id,
            rule_id=case.rule_id,
            llm_explanation=llm_explanation,
            llm_fix=llm_fix,
            verified=verified,
            similarity=round(similarity, 4),
            keyword_match=kw_match,
            score=score,
            elapsed_seconds=round(time.perf_counter() - start, 2),
        )

    @staticmethod
    def _estimate_violation_line(case: EvalCase) -> int:
        """Pick a sensible default line number — middle of the bad_code."""
        line_count = case.bad_code.count("\n") or 1
        return max(1, line_count // 2)

    @staticmethod
    def _extract_code(fix_suggestion: str) -> str | None:
        if not fix_suggestion:
            return None
        fences = _FENCE_RE.findall(fix_suggestion)
        if fences:
            return fences[-1].strip()
        # No fence — if there's a blank-line separator, take the second half
        parts = fix_suggestion.split("\n\n", 1)
        if len(parts) == 2:
            return parts[1].strip()
        return fix_suggestion.strip()

    @staticmethod
    def _extract_explanation(fix_suggestion: str) -> str | None:
        if not fix_suggestion:
            return None
        # Convention: explanation is everything before the first fence
        m = _FENCE_RE.search(fix_suggestion)
        if m:
            return fix_suggestion[: m.start()].strip() or None
        # No fence — assume first paragraph is the explanation
        parts = fix_suggestion.split("\n\n", 1)
        return parts[0].strip() if parts else None

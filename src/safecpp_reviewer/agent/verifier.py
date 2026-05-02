"""Apply LLM-proposed fixes and verify they resolve the original violation.

The verifier writes a patched copy of the source file to a temporary
directory, re-runs clang-tidy on it, and checks whether the original
violation's rule_id still appears within the patched chunk's line range.

Limitations (deliberate, documented):
    - Chunk-level patching only.  The whole chunk's text is replaced with
      the LLM's ``fixed_code``.  Per-line surgical patches are out of scope.
    - Line-shift tolerance.  We don't track byte offsets — we just check
      whether any violation with the same rule_id appears anywhere in the
      patched chunk's range, expanded by a small margin.
    - Single-tool verification.  Only clang-tidy is re-run; cppcheck would
      be straightforward to add but isn't wired up yet.
    - Size sanity check.  When a chunk is provided, we reject fixes that
      are dramatically smaller than the original chunk because 7B models
      often return only the changed lines instead of the complete chunk.
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
from pathlib import Path

from safecpp_reviewer.agent.models import VerificationResult
from safecpp_reviewer.analyzer.clang_tidy import ClangTidyRunner
from safecpp_reviewer.analyzer.models import Violation
from safecpp_reviewer.chunker.models import Chunk

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:cpp|c\+\+)?\s*\n(.*?)```", re.DOTALL)


class FixVerifier:
    """Applies an LLM-proposed fix to source and re-runs clang-tidy.

    Args:
        clang_tidy: A configured :class:`ClangTidyRunner` to re-run on the
            patched file.
        line_margin: Number of lines above/below the original violation line
            to still count as "the same violation".
        min_size_ratio: When verifying with a chunk, reject fixes whose
            line count is less than this fraction of the original chunk's
            line count.  Catches the common 7B-model failure mode of
            returning only the changed lines instead of the whole chunk.
            Set to ``0.0`` to disable the check.
    """

    def __init__(
        self,
        clang_tidy: ClangTidyRunner | None = None,
        line_margin: int = 5,
        min_size_ratio: float = 0.4,
    ) -> None:
        self.clang_tidy = clang_tidy or ClangTidyRunner()
        self.line_margin = line_margin
        self.min_size_ratio = min_size_ratio

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify(
        self,
        violation: Violation,
        chunk: Chunk | None = None,
    ) -> VerificationResult:
        """Verify the fix attached to *violation*."""
        if violation.fix_suggestion is None:
            return VerificationResult(resolved=False, error="No fix_suggestion on violation")

        try:
            fixed_code = self._extract_code(violation.fix_suggestion)
        except ValueError as exc:
            return VerificationResult(resolved=False, error=str(exc))

        # ---- Size sanity check (Option A from review)
        if chunk is not None and self.min_size_ratio > 0.0:
            original_lines = chunk.content.count("\n") or 1
            fix_lines = fixed_code.count("\n") or 1
            ratio = fix_lines / original_lines
            if ratio < self.min_size_ratio:
                logger.info(
                    "verifier: rejecting fix for %s — %d lines vs chunk's %d "
                    "(ratio %.2f < %.2f); likely partial fix",
                    violation.rule_id,
                    fix_lines,
                    original_lines,
                    ratio,
                    self.min_size_ratio,
                )
                return VerificationResult(
                    resolved=False,
                    error=(
                        f"fix is {fix_lines} lines vs chunk's {original_lines} "
                        f"({ratio:.0%}); likely a partial fix that would corrupt "
                        f"the file"
                    ),
                )

        try:
            return self._verify_with_patch(violation, chunk, fixed_code)
        except Exception as exc:
            logger.warning("verification crashed: %s", exc)
            return VerificationResult(resolved=False, error=str(exc))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _extract_code(self, fix_suggestion: str) -> str:
        """Pull the raw C++ code out of a fix_suggestion string."""
        fences = _FENCE_RE.findall(fix_suggestion)
        if fences:
            return fences[-1].strip()

        parts = fix_suggestion.split("\n\n", 1)
        candidate = parts[1] if len(parts) > 1 else fix_suggestion
        candidate = candidate.strip()
        if not candidate:
            raise ValueError("fix_suggestion contained no code")
        return candidate

    def _verify_with_patch(
        self,
        violation: Violation,
        chunk: Chunk | None,
        fixed_code: str,
    ) -> VerificationResult:
        original = Path(violation.file)
        if not original.exists():
            return VerificationResult(resolved=False, error=f"source file missing: {original}")

        original_lines = original.read_text(encoding="utf-8").splitlines(keepends=True)

        if chunk is not None:
            patched_lines = self._patch_chunk(original_lines, chunk, fixed_code)
            patched_range = (chunk.start_line, chunk.end_line)
        else:
            patched_lines = self._patch_line(original_lines, violation.line, fixed_code)
            patched_range = (
                max(1, violation.line - 1),
                violation.line + fixed_code.count("\n") + 1,
            )

        return self._run_clang_tidy_on_patch(
            original=original,
            patched_lines=patched_lines,
            violation=violation,
            patched_range=patched_range,
        )

    @staticmethod
    def _patch_chunk(original_lines: list[str], chunk: Chunk, fixed_code: str) -> list[str]:
        before = original_lines[: chunk.start_line - 1]
        after = original_lines[chunk.end_line :]
        if not fixed_code.endswith("\n"):
            fixed_code += "\n"
        return before + [fixed_code] + after

    @staticmethod
    def _patch_line(original_lines: list[str], line: int, fixed_code: str) -> list[str]:
        idx = max(0, line - 1)
        if idx >= len(original_lines):
            return original_lines + [fixed_code]
        if not fixed_code.endswith("\n"):
            fixed_code += "\n"
        return original_lines[:idx] + [fixed_code] + original_lines[idx + 1 :]

    def _run_clang_tidy_on_patch(
        self,
        original: Path,
        patched_lines: list[str],
        violation: Violation,
        patched_range: tuple[int, int],
    ) -> VerificationResult:
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            patched_file = tmp_dir / original.name
            patched_file.write_text("".join(patched_lines), encoding="utf-8")

            for sibling in original.parent.iterdir():
                if sibling == original or sibling.is_dir():
                    continue
                target = tmp_dir / sibling.name
                if target.exists():
                    continue
                try:
                    target.symlink_to(sibling)
                except OSError:
                    shutil.copy2(sibling, target)

            try:
                new_violations = self.clang_tidy.run(patched_file)
            except RuntimeError as exc:
                return VerificationResult(resolved=False, error=str(exc))

        return self._classify(violation, new_violations, patched_range)

    def _classify(
        self,
        original_violation: Violation,
        new_violations: list[Violation],
        patched_range: tuple[int, int],
    ) -> VerificationResult:
        start = max(1, patched_range[0] - self.line_margin)
        end = patched_range[1] + self.line_margin

        original_still_present = any(
            v.rule_id == original_violation.rule_id and start <= v.line <= end
            for v in new_violations
        )

        regressions = [
            v
            for v in new_violations
            if v.rule_id != original_violation.rule_id and start <= v.line <= end
        ]

        return VerificationResult(
            resolved=not original_still_present,
            new_violations=regressions,
        )

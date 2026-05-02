"""clang-tidy subprocess wrapper.

Runs clang-tidy on a single translation unit and parses the output into
a list of :class:`~safecpp_reviewer.analyzer.models.Violation` objects.

Typical usage::

    from pathlib import Path
    from safecpp_reviewer.analyzer.clang_tidy import ClangTidyRunner

    runner = ClangTidyRunner(checks="cppcoreguidelines-*,modernize-*")
    violations = runner.run(Path("src/foo.cpp"))
"""

import logging
import re
import subprocess
import typing
from pathlib import Path

from safecpp_reviewer.analyzer.models import Violation

logger = logging.getLogger(__name__)

# clang-tidy diagnostic line:
#   /path/to/file.cpp:12:34: warning: some message [check-name]
_DIAG_RE: typing.Final = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+):\s+"
    r"(?P<sev>error|warning|note|remark):\s+"
    r"(?P<msg>.+?)\s+\[(?P<rule>[^\]]+)\]$"
)

_SEV_MAP: typing.Final[dict[str, str]] = {
    "error": "error",
    "warning": "warning",
    "note": "note",
    "remark": "note",
}


def _infer_category(rule_id: str) -> str | None:
    """Derive a category string from the clang-tidy check name."""
    prefixes: typing.Final = {
        "cppcoreguidelines": "cppcoreguidelines",
        "modernize": "modernize",
        "readability": "readability",
        "performance": "performance",
        "bugprone": "bugprone",
        "clang-analyzer": "clang-analyzer",
        "misc": "misc",
    }
    for prefix, category in prefixes.items():
        if rule_id.startswith(prefix):
            return category
    return None


class ClangTidyRunner:
    """Wraps ``clang-tidy`` and returns structured :class:`Violation` objects.

    Args:
        executable: Path or name of the clang-tidy binary.
        checks: Comma-separated check glob, e.g. ``"cppcoreguidelines-*,modernize-*"``.
        extra_args: Extra compiler flags passed after ``--``, e.g. ``["-std=c++17"]``.
        header_filter: Regex for which headers to include in diagnostics.
            Defaults to empty string (source file only — avoids header noise).
    """

    def __init__(
        self,
        executable: str = "clang-tidy",
        checks: str = "cppcoreguidelines-*,modernize-*,readability-*,bugprone-*",
        extra_args: list[str] | None = None,
        header_filter: str = "",
    ) -> None:
        self.executable = executable
        self.checks = checks
        self.extra_args = extra_args or ["-std=c++17"]
        self.header_filter = header_filter

    def run(self, source_file: Path) -> list[Violation]:
        """Run clang-tidy on *source_file* and return all violations found.

        Args:
            source_file: Path to the ``.cpp`` file to analyse.

        Returns:
            List of :class:`Violation` objects (may be empty).

        Raises:
            FileNotFoundError: If *source_file* does not exist.
            RuntimeError: If the clang-tidy binary cannot be found.
        """
        if not source_file.exists():
            raise FileNotFoundError(f"Source file not found: {source_file}")

        cmd: typing.Final = [
            self.executable,
            f"--checks={self.checks}",
            f"--header-filter={self.header_filter}",
            str(source_file),
            "--",
            *self.extra_args,
        ]

        logger.debug("clang-tidy cmd: %s", " ".join(cmd))

        try:
            result: typing.Final = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"clang-tidy binary not found: {self.executable!r}. "
                "Install via `apt install clang-tidy` or `brew install llvm`."
            ) from exc

        output: typing.Final = result.stdout + result.stderr
        violations: typing.Final = self._parse(output, source_file)

        logger.info(
            "clang-tidy: %d violation(s) in %s (exit %d)",
            len(violations),
            source_file.name,
            result.returncode,
        )
        return violations

    def _parse(self, output: str, source_file: Path) -> list[Violation]:
        violations: typing.Final[list[Violation]] = []
        for raw_line in output.splitlines():
            m = _DIAG_RE.match(raw_line.strip())
            if not m:
                continue

            # Only keep diagnostics that point at the file we analysed.
            diag_file = Path(m.group("file"))
            if diag_file.resolve() != source_file.resolve():
                continue

            rule_id = f"clang-tidy:{m.group('rule')}"
            sev_raw = m.group("sev")
            severity = _SEV_MAP.get(sev_raw, "warning")

            violations.append(
                Violation(
                    file=diag_file,
                    line=int(m.group("line")) - 1,
                    column=int(m.group("col")),
                    rule_id=rule_id,
                    severity=severity,  # type: ignore[arg-type]
                    message=m.group("msg"),
                    tool="clang-tidy",
                    category=_infer_category(m.group("rule")),
                )
            )

        return violations

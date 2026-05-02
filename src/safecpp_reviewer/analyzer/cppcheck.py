"""cppcheck subprocess wrapper.

Runs cppcheck on a single file using XML output (v2) and parses the result
into :class:`~safecpp_reviewer.analyzer.models.Violation` objects.

Typical usage::

    from pathlib import Path
    from safecpp_reviewer.analyzer.cppcheck import CppcheckRunner

    runner = CppcheckRunner()
    violations = runner.run(Path("src/foo.cpp"))
"""

import logging
import subprocess
import typing
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

from safecpp_reviewer.analyzer.models import Violation

logger = logging.getLogger(__name__)

# cppcheck severity → our normalized severity
_SEV_MAP: typing.Final[dict[str, Literal["error", "warning", "note", "style"]]] = {
    "error": "error",
    "warning": "warning",
    "style": "style",
    "performance": "warning",
    "portability": "warning",
    "information": "note",
    "debug": "note",
}


def _infer_category(rule_id: str) -> str | None:
    """Best-effort category from the cppcheck errorId."""
    misra_prefixes: typing.Final = ("misra", "MISRA")
    autosar_prefixes: typing.Final = ("autosar", "AUTOSAR")

    if any(rule_id.startswith(p) for p in misra_prefixes):
        return "MISRA"
    if any(rule_id.startswith(p) for p in autosar_prefixes):
        return "AUTOSAR"
    return "cppcheck"


class CppcheckRunner:
    """Wraps ``cppcheck`` and returns structured :class:`Violation` objects.

    Args:
        executable: Path or name of the cppcheck binary.
        enable: Comma-separated checks to enable (default: all).
        std: C++ standard to pass to cppcheck, e.g. ``"c++17"``.
        extra_args: Additional CLI flags forwarded verbatim to cppcheck.
    """

    def __init__(
        self,
        executable: str = "cppcheck",
        enable: str = "all",
        std: str = "c++17",
        extra_args: list[str] | None = None,
    ) -> None:
        self.executable = executable
        self.enable = enable
        self.std = std
        self.extra_args = extra_args or []

    def run(self, source_file: Path) -> list[Violation]:
        """Run cppcheck on *source_file* and return all violations found.

        Args:
            source_file: Path to the ``.cpp`` (or ``.hpp``) file to analyse.

        Returns:
            List of :class:`Violation` objects (may be empty).

        Raises:
            FileNotFoundError: If *source_file* does not exist.
            RuntimeError: If the cppcheck binary cannot be found.
        """
        if not source_file.exists():
            raise FileNotFoundError(f"Source file not found: {source_file}")

        cmd: typing.Final = [
            self.executable,
            "--xml",
            "--xml-version=2",
            f"--enable={self.enable}",
            f"--std={self.std}",
            "--inline-suppr",  # respect //cppcheck-suppress comments
            "--suppress=missingIncludeSystem",
            *self.extra_args,
            str(source_file),
        ]

        logger.debug("cppcheck cmd: %s", " ".join(cmd))

        try:
            result: typing.Final = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                check=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"cppcheck binary not found: {self.executable!r}. "
                "Install via `apt install cppcheck` or `brew install cppcheck`."
            ) from exc

        # cppcheck writes XML to stderr
        violations: typing.Final = self._parse_xml(result.stderr, source_file)

        logger.info(
            "cppcheck: %d violation(s) in %s (exit %d)",
            len(violations),
            source_file.name,
            result.returncode,
        )
        return violations

    def _parse_xml(self, xml_output: str, source_file: Path) -> list[Violation]:
        violations: typing.Final[list[Violation]] = []

        try:
            root: typing.Final = ET.fromstring(xml_output)
        except ET.ParseError:
            logger.warning("cppcheck produced invalid XML — no violations parsed")
            return violations

        for error in root.iter("error"):
            error_id = error.get("id", "unknown")
            severity_raw = error.get("severity", "warning")
            message = error.get("msg", "")
            verbose = error.get("verbose", message)

            severity = _SEV_MAP.get(severity_raw, "warning")
            rule_id = f"cppcheck:{error_id}"

            # Each <error> can have multiple <location> elements; use the first.
            location = error.find("location")
            if location is None:
                continue

            loc_file = Path(location.get("file", ""))
            # Skip errors not pointing at our file (e.g. system headers).
            if loc_file.resolve() != source_file.resolve():
                continue

            line_str = location.get("line", "1")
            col_str = location.get("column")

            violations.append(
                Violation(
                    file=loc_file,
                    line=max(
                        0, int(line_str) - 1
                    ),  # cppcheck lines are 1-based; convert to 0-based
                    column=int(col_str) if col_str and int(col_str) >= 1 else None,
                    rule_id=rule_id,
                    severity=severity,
                    message=verbose or message,
                    tool="cppcheck",
                    category=_infer_category(error_id),
                )
            )

        return violations

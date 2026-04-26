import subprocess
from pathlib import Path

from .models import Violation


def run_clang_tidy(file_path: Path) -> list[Violation]:
    cmd = [
        "clang-tidy",
        str(file_path),
        "--export-fixes=fixes.yaml",
        "--",
        "-std=c++17",
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    # stdout parsing (temporary)
    violations = []

    for line in result.stdout.splitlines():
        if "warning:" in line or "error:" in line:
            # placeholder parsing
            violations.append(
                Violation(
                    tool="clang-tidy",
                    file_path=Path(file_path),
                    line=0,
                    column=None,
                    severity="warning",
                    rule_id="unknown",
                    message=line,
                )
            )

    return violations

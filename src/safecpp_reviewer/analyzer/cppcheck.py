import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from .models import Violation


def run_cppcheck(file_path: Path) -> list[Violation]:

    cmd = [
        "cppcheck",
        "--enable=all",
        "--xml",
        str(file_path),
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    violations: list[Violation] = []

    if not result.stderr.strip():
        return violations

    root = ET.fromstring(result.stderr)

    for error in root.findall(".//error"):
        # SAFE location handling
        location = error.find("location")

        if location is None:
            continue  # ← CRITICAL FIX

        file_attr = location.get("file")
        line_attr = location.get("line")

        if file_attr is None or line_attr is None:
            continue  # ← ALSO IMPORTANT

        violations.append(
            Violation(
                tool="cppcheck",
                file_path=file_attr,
                line=int(line_attr),
                column=None,
                severity=error.get(
                    "severity",
                    "warning",
                ),
                rule_id=error.get(
                    "id",
                    "unknown",
                ),
                message=error.get(
                    "msg",
                    "",
                ),
                category=error.get("category"),
            )
        )

    return violations

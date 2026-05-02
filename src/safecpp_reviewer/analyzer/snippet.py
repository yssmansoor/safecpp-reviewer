import typing

from safecpp_reviewer.analyzer.models import Violation


def extract_snippet(violation: Violation, context_lines: int = 2) -> str | None:
    """Read *context_lines* above and below violation.line from violation.file.
    Returns None if the file can't be read.
    """
    try:
        with open(violation.file, encoding="utf-8") as f:
            lines: typing.Final = f.readlines()
            start: typing.Final = max(0, violation.line - context_lines - 1)
            end: typing.Final = min(len(lines), violation.line + context_lines)
            output_lines: typing.Final = []
            for i in range(start, end):
                prefix = ">>>" if i == violation.line else "   "
                line_no = i + 1
                output_lines.append(f"{prefix} {line_no:4d} | {lines[i].rstrip()}")
            return "\n".join(output_lines)
    except (FileNotFoundError, PermissionError, IsADirectoryError, UnicodeDecodeError) as e:
        print(f"Error reading file {violation.file}: {e}")
        return None

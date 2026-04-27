from pathlib import Path

from safecpp_reviewer.analyzer import run_all

violations = run_all(Path("tests/fixtures/sample_violations.cpp"))
for v in violations:
    print(v.short())

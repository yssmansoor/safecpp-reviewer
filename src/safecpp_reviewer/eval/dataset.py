"""Load eval cases from YAML files in :mod:`safecpp_reviewer.eval.data`."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from safecpp_reviewer.eval.models import EvalCase

logger = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = Path(__file__).parent / "data"


def load_cases(directory: Path | None = None) -> list[EvalCase]:
    """Load every YAML file in *directory* (or the bundled data dir) into
    a single list of :class:`EvalCase` objects.

    Each YAML file may be:
        - A list of case objects, or
        - A dict with a ``cases`` key containing the list.
    """
    target = directory or _DEFAULT_DATA_DIR

    if not target.exists():
        logger.warning("Eval data directory does not exist: %s", target)
        return []

    cases: list[EvalCase] = []
    for path in sorted(target.glob("*.y*ml")):
        try:
            cases.extend(_load_yaml_file(path))
        except Exception as exc:
            logger.warning("Failed to load %s: %s", path, exc)

    logger.info("Loaded %d eval case(s) from %s", len(cases), target)
    return cases


def _load_yaml_file(path: Path) -> list[EvalCase]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []

    if isinstance(raw, dict):
        entries = raw.get("cases", [raw])
    elif isinstance(raw, list):
        entries = raw
    else:
        raise ValueError(f"Expected dict or list in {path}")

    return [EvalCase.model_validate(e) for e in entries]

"""In-memory store for static analysis rule documentation.

Loads YAML files from :mod:`safecpp_reviewer.knowledge.data` (or any directory
you point it at) and provides exact-match lookup by ``rule_id``.

Typical usage::

    store = RuleStore.from_default_data()
    rule = store.get("clang-tidy:cppcoreguidelines-pro-type-cstyle-cast")
    if rule is not None:
        prompt += rule.for_prompt()
"""

from __future__ import annotations

import logging
import typing
from collections.abc import Iterable
from pathlib import Path

import yaml

from safecpp_reviewer.knowledge.models import Rule

logger = logging.getLogger(__name__)

# Default location: <package>/knowledge/data/
_DEFAULT_DATA_DIR: typing.Final = Path(__file__).parent / "data"


class RuleStore:
    """Loads rules from YAML and offers exact-match lookup by ``rule_id``."""

    def __init__(self, rules: Iterable[Rule] = ()) -> None:
        self._rules: dict[str, Rule] = {r.rule_id: r for r in rules}

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_directory(cls, directory: Path) -> RuleStore:
        """Load every ``*.yaml`` / ``*.yml`` file in *directory* into one store.

        Each YAML file may contain either a single rule object or a list of
        rule objects.  Files that fail to parse are skipped with a warning.
        """
        if not directory.exists():
            logger.warning("Rule directory does not exist: %s", directory)
            return cls()

        all_rules: typing.Final[list[Rule]] = []
        for path in sorted(directory.glob("*.y*ml")):
            try:
                all_rules.extend(_load_yaml_file(path))
            except Exception as exc:
                logger.warning("Failed to load %s: %s", path, exc)

        logger.info("Loaded %d rules from %s", len(all_rules), directory)
        return cls(all_rules)

    @classmethod
    def from_default_data(cls) -> RuleStore:
        """Load rules from the bundled ``knowledge/data/`` directory."""
        return cls.from_directory(_DEFAULT_DATA_DIR)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, rule_id: str) -> Rule | None:
        """Return the rule for *rule_id*, or ``None`` if unknown."""
        return self._rules.get(rule_id)

    def __contains__(self, rule_id: str) -> bool:
        return rule_id in self._rules

    def __len__(self) -> int:
        return len(self._rules)

    @property
    def rule_ids(self) -> list[str]:
        return list(self._rules.keys())


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _load_yaml_file(path: Path) -> list[Rule]:
    """Parse a single YAML file into a list of :class:`Rule` objects."""
    raw: typing.Final = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []

    if isinstance(raw, dict):
        # Support {"rules": [...]} or a single rule object
        if "rules" in raw and isinstance(raw["rules"], list):
            entries = raw["rules"]
        else:
            entries = [raw]
    elif isinstance(raw, list):
        entries = raw
    else:
        raise ValueError(f"Expected dict or list in {path}, got {type(raw).__name__}")

    return [Rule.model_validate(entry) for entry in entries]


# from __future__ import annotations

# from pathlib import Path
# from typing import Any

# import yaml

# from safecpp_reviewer.analyzer.models import Violation
# from safecpp_reviewer.knowledge.models import RuleInfo


# class RuleStore:
#     """Lookup table for rule-specific review guidance."""

#     def __init__(self, rules: list[RuleInfo] | None = None) -> None:
#         self._rules: dict[str, RuleInfo] = {}
#         for rule in rules or []:
#             self.add(rule)

#     @classmethod
#     def from_default_data(cls) -> RuleStore:
#         data_dir = Path(__file__).with_name("data")
#         return cls.from_directory(data_dir)

#     @classmethod
#     def from_directory(cls, data_dir: Path) -> RuleStore:
#         store = cls()
#         if not data_dir.exists():
#             return store

#         for path in sorted(data_dir.glob("*.yaml")):
#             store.extend(cls._load_yaml_file(path))

#         return store

#     def add(self, rule: RuleInfo) -> None:
#         for key in self._keys_for_rule_id(rule.rule_id):
#             self._rules[key] = rule

#     def extend(self, rules: list[RuleInfo]) -> None:
#         for rule in rules:
#             self.add(rule)

#     def lookup(self, rule_id: str) -> RuleInfo | None:
#         for key in self._keys_for_rule_id(rule_id):
#             rule = self._rules.get(key)
#             if rule is not None:
#                 return rule
#         return None

#     def format_for_violations(self, violations: list[Violation]) -> str:
#         seen: set[str] = set()
#         lines: list[str] = []

#         for violation in violations:
#             rule = self.lookup(violation.rule_id)
#             if rule is None or rule.rule_id in seen:
#                 continue
#             seen.add(rule.rule_id)
#             lines.append(rule.format_for_prompt())

#         return "\n\n".join(lines)

#     @staticmethod
#     def _keys_for_rule_id(rule_id: str) -> list[str]:
#         normalized = rule_id.strip()
#         keys = [normalized]
#         if ":" in normalized:
#             keys.append(normalized.rsplit(":", maxsplit=1)[-1])
#         return keys

#     @staticmethod
#     def _load_yaml_file(path: Path) -> list[RuleInfo]:
#         data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.stat().st_size else None
#         if data is None:
#             return []

#         raw_rules: list[Any]
#         if isinstance(data, dict):
#             rules_value = data.get("rules", data)
#             if isinstance(rules_value, dict):
#                 raw_rules = [
#                     {"rule_id": rule_id, **value}
#                     if isinstance(value, dict)
#                     else {"rule_id": rule_id, "summary": str(value)}
#                     for rule_id, value in rules_value.items()
#                 ]
#             elif isinstance(rules_value, list):
#                 raw_rules = rules_value
#             else:
#                 raw_rules = []
#         elif isinstance(data, list):
#             raw_rules = data
#         else:
#             raw_rules = []

#         rules: list[RuleInfo] = []
#         for item in raw_rules:
#             if not isinstance(item, dict):
#                 continue

#             rule_id = item.get("rule_id") or item.get("id") or item.get("name")
#             if not isinstance(rule_id, str):
#                 continue

#             rules.append(
#                 RuleInfo(
#                     rule_id=rule_id,
#                     title=cls_str(item.get("title")),
#                     summary=cls_str(item.get("summary") or item.get("description")),
#                     fix=cls_str(item.get("fix") or item.get("recommendation")),
#                 )
#             )

#         return rules


# def cls_str(value: object) -> str | None:
#     if value is None:
#         return None
#     return str(value)

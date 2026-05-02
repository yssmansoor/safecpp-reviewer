import typing

from safecpp_reviewer.analyzer.models import Violation
from safecpp_reviewer.chunker.models import Chunk
from safecpp_reviewer.knowledge import RuleStore

SYSTEM_PROMPT: typing.Final = """You are a safety-critical C++ code reviewer specializing in
MISRA C++, AUTOSAR, and CppCoreGuidelines.

Respond ONLY with valid JSON matching this schema:
{
  "explanation": "<1-3 sentences explaining why this is a violation>",
  "fixed_code": "<the corrected code, plain text only>"
}

CRITICAL rules:
- "fixed_code" MUST be plain C++ code only — NO markdown fences, NO triple backticks, NO "cpp" prefix
- "fixed_code" MUST contain real newline characters (\\n in JSON)
- DO NOT include the original buggy code, only the corrected version
- DO NOT use single backticks anywhere

Example correct output:
{"explanation": "The cast is redundant since a + b is already int.", "fixed_code": "int result = a + b;\\nreturn result;"}

No preamble. No markdown around the JSON. Just the JSON object."""


CHUNK_SYSTEM_PROMPT: typing.Final = """You are a safety-critical C++ code reviewer specializing in
MISRA C++, AUTOSAR, and CppCoreGuidelines.

Respond ONLY with valid JSON matching this schema:
{
  "reviews": [
    {
      "violation_index": 0,
      "explanation": "<1-3 sentences explaining this violation>",
      "fixed_code": "<the corrected code for this violation, plain text only>"
    }
  ]
}

CRITICAL rules:
- Return exactly one item in "reviews" for each violation you were given.
- "violation_index" MUST match the zero-based index shown in the prompt.
- "fixed_code" MUST be plain C++ code only — NO markdown fences, NO triple backticks, NO "cpp" prefix
- "fixed_code" MUST contain real newline characters (\\n in JSON)
- DO NOT include the original buggy code unless it is part of the corrected replacement
- DO NOT use single backticks anywhere

Example correct output:
{"reviews":[{"violation_index":0,"explanation":"The cast is redundant since a + b is already int.","fixed_code":"int result = a + b;"}]}

No preamble. No markdown around the JSON. Just the JSON object."""


def _violation_for_prompt(v: Violation, index: int | None = None) -> str:

    prefix: typing.Final = f"[{index}] " if index is not None else ""
    return f"{prefix}line {v.line}: {v.rule_id} ({v.severity})\n  message: {v.message}"


def _rule_guidance_for_prompt(rule_store: RuleStore | None, violations: list[Violation]) -> str:
    if rule_store is None:
        return ""

    all_guidance_parts: list[str] = []
    for v in violations:
        rule = rule_store.get(v.rule_id)
        if rule:
            all_guidance_parts.append(rule.for_prompt())
    guidance: typing.Final = "\n\n".join(all_guidance_parts)

    if not guidance:
        return ""

    return f"\n\nRule guidance:\n{guidance}"


def build_user_prompt(violation: Violation, store: RuleStore) -> str:
    """Build a prompt for reviewing a single violation."""
    rule: typing.Final = store.get(violation.rule_id)
    rule_context: typing.Final = f"\n\nRule documentation:\n{rule.for_prompt()}\n" if rule else ""
    snippet: typing.Final = (
        f"\n\nCode context:\n{violation.code_snippet}" if violation.code_snippet else ""
    )
    return f"""Review this C++ violation. Respond with the required JSON only.

{_violation_for_prompt(violation)}{snippet}{rule_context}"""


def build_chunk_user_prompt(chunk: Chunk, violations: list[Violation], store: RuleStore) -> str:
    """Build a prompt for reviewing multiple violations in the same chunk together."""
    blocks: typing.Final[list[str]] = []
    for i, v in enumerate(violations):
        parts = [_violation_for_prompt(v, i)]

        if v.code_snippet:
            parts.append(f"Code context:\n{v.code_snippet}")

        rule = store.get(v.rule_id)
        if rule:
            parts.append(f"Rule guidance:\n{rule.for_prompt()}")

        blocks.append("\n\n".join(parts))

    violation_texts: typing.Final = "\n\n---\n\n".join(blocks)

    return f"""Review these C++ violations together. Consider interactions within the same function or class. Respond with the required JSON only.

Chunk: {chunk.chunk_type} {chunk.name or "<anonymous>"} \
({chunk.file}:{chunk.start_line}-{chunk.end_line})

Code:
{chunk.content}

Violations:
{violation_texts}"""

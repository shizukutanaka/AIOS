"""aictl schema lint — design review for constrained-decoding JSON Schemas.

`guided validate` answers "does this document match this schema?". Nothing
answered the prior question: "is this schema one a model can fill in well?"

That question matters because grammar-constrained generation gives a **format
guarantee, not a semantic one**. The output is always structurally valid; it
is not always correct. A schema can be perfectly valid JSON Schema, compile
fine in XGrammar, produce parseable output on every request — and still make
the answers worse. Those failures are invisible precisely because the
structural check passes.

The checks here are the design mistakes that benchmark and practitioner
reporting keeps naming, restricted to ones detectable from the schema alone:

  * **answer-before-reasoning ordering** — the load-bearing one. Generation is
    autoregressive, so a schema that emits `answer` before `reasoning` forces
    the model to commit to a conclusion and then rationalize it. The model
    cannot think first if the grammar will not let it write first.
  * **deep nesting** (4+ levels) and **very wide schemas** (50+ fields), both
    associated with higher error rates.
  * **undescribed fields** — the description is the only place a schema says
    what a field *means*; without it the model is guessing from the key name.
  * **optional fields that cannot be null** — a field the model is required
    to emit but has no value for leaves it inventing one, since the grammar
    forbids omitting it and forbids null.

Every finding is advisory. This never rejects a schema: plenty of good
schemas break these rules deliberately, and a linter that blocks deployment
on a heuristic is worse than one that explains itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Fields that carry the model's working-out. Kept deliberately short and
# unambiguous — a false positive here tells someone their schema is wrong when
# it isn't, which is worse than staying quiet.
_REASONING_KEYS = frozenset({
    "reasoning", "rationale", "thinking", "thought", "thoughts",
    "explanation", "analysis", "justification", "scratchpad", "chain_of_thought",
})

# Fields that carry the conclusion.
_ANSWER_KEYS = frozenset({
    "answer", "result", "conclusion", "output", "verdict", "decision",
    "classification", "label", "score", "final_answer",
})

MAX_NESTING_DEPTH = 4
MAX_FIELD_COUNT = 50


@dataclass
class Finding:
    """One design observation. `rule` is stable; `message` is for humans."""
    rule: str
    severity: str        # "warn" | "info"
    message: str
    path: str = ""       # dotted location in the schema, "" for whole-schema

    def to_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "severity": self.severity,
                "message": self.message, "path": self.path}


def _properties(node: Any) -> dict[str, Any]:
    props = node.get("properties") if isinstance(node, dict) else None
    return props if isinstance(props, dict) else {}


def _walk(node: Any, path: str = "", depth: int = 1):
    """Yield (path, depth, property_name, subschema) for every property."""
    for name, sub in _properties(node).items():
        here = f"{path}.{name}" if path else name
        yield here, depth, name, sub
        if isinstance(sub, dict):
            if sub.get("type") == "array" and isinstance(sub.get("items"), dict):
                yield from _walk(sub["items"], f"{here}[]", depth + 1)
            else:
                yield from _walk(sub, here, depth + 1)


def _check_reasoning_order(schema: dict[str, Any]) -> list[Finding]:
    """Flag a conclusion field declared before the reasoning that supports it.

    Checked on `properties` declaration order, and on `required` order too:
    some backends emit in the order `required` lists rather than the order
    `properties` declares.
    """
    findings: list[Finding] = []
    for label, keys in (("properties", list(_properties(schema).keys())),
                        ("required", [k for k in schema.get("required", [])
                                      if isinstance(k, str)])):
        lowered = [k.lower() for k in keys]
        answer_at = next((i for i, k in enumerate(lowered) if k in _ANSWER_KEYS), None)
        reason_at = next((i for i, k in enumerate(lowered) if k in _REASONING_KEYS), None)
        if answer_at is None or reason_at is None or reason_at <= answer_at:
            continue
        findings.append(Finding(
            rule="answer_before_reasoning",
            severity="warn",
            path=label,
            message=(f"'{keys[answer_at]}' is declared before "
                     f"'{keys[reason_at]}' in {label}. Generation is "
                     "autoregressive, so the model must commit to the answer "
                     "before writing the reasoning that supposedly produced "
                     "it — the reasoning becomes a rationalization. Put the "
                     "reasoning field first."),
        ))
    return findings


def lint_schema(schema: Any) -> list[Finding]:
    """Review a JSON Schema for constrained-decoding design problems.

    Never raises: a malformed schema yields a finding, not an exception.
    """
    if not isinstance(schema, dict):
        return [Finding(rule="not_an_object", severity="warn",
                        message="Schema is not a JSON object, so no design "
                                "review is possible.")]

    findings = _check_reasoning_order(schema)
    walked = list(_walk(schema))

    deepest = max((d for _p, d, _n, _s in walked), default=0)
    if deepest >= MAX_NESTING_DEPTH:
        findings.append(Finding(
            rule="deep_nesting", severity="warn",
            message=(f"Schema nests {deepest} levels deep (at or beyond "
                     f"{MAX_NESTING_DEPTH}). Deeply nested structures are "
                     "associated with higher error rates; flattening usually "
                     "costs nothing and helps."),
        ))

    if len(walked) >= MAX_FIELD_COUNT:
        findings.append(Finding(
            rule="too_many_fields", severity="warn",
            message=(f"Schema declares {len(walked)} fields (at or beyond "
                     f"{MAX_FIELD_COUNT}). Very wide schemas degrade output "
                     "quality; consider splitting into separate calls."),
        ))

    undescribed = [p for p, _d, _n, sub in walked
                   if isinstance(sub, dict) and not str(sub.get("description", "")).strip()]
    if undescribed:
        shown = ", ".join(undescribed[:5])
        more = f" (+{len(undescribed) - 5} more)" if len(undescribed) > 5 else ""
        findings.append(Finding(
            rule="missing_descriptions", severity="info",
            message=(f"{len(undescribed)} field(s) have no description: {shown}"
                     f"{more}. The description is the only place the schema "
                     "says what a field means — without it the model infers "
                     "from the key name alone."),
        ))

    required = {k for k in schema.get("required", []) if isinstance(k, str)}
    optional_not_nullable = []
    for name, sub in _properties(schema).items():
        if name in required or not isinstance(sub, dict):
            continue
        declared = sub.get("type")
        types = declared if isinstance(declared, list) else [declared]
        if "null" not in types and sub.get("anyOf") is None and sub.get("oneOf") is None:
            optional_not_nullable.append(name)
    if optional_not_nullable:
        findings.append(Finding(
            rule="optional_without_null", severity="info",
            message=(f"Optional field(s) {', '.join(optional_not_nullable[:5])} "
                     "cannot be null. If the model has no value it can neither "
                     "omit the field nor say so, and will invent one. Allow "
                     "null explicitly."),
        ))

    return findings

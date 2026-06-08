"""Guided / structured-decoding advisor + stdlib JSON-Schema validator.

aictl emits schema-shaped data everywhere (``--json`` on every command, the
JSON-RPC MCP server) yet, until now, offered no advice on *making the model*
produce valid structured output. This module fills backlog item **K**:

  1. an **engine → backend support matrix** (XGrammar / Outlines / llguidance /
     lm-format-enforcer) with ready-to-paste serve flags, and
  2. a dependency-free **JSON-Schema validator** (a practical subset) so SDK and
     MCP callers can *enforce* structured outputs locally — no external dep.

State of the field (2026): **XGrammar** is the default structured-generation
backend for vLLM, SGLang and TensorRT-LLM (<40µs/token); **llguidance**
(Rust Earley, ~50µs), **Outlines** (regex→FSM) and **lm-format-enforcer** are
the common alternatives. JSONSchemaBench (arXiv:2501.10868) benchmarks them
over 10K real schemas. This is an advisor, not a runtime — it recommends the
backend and prints the flags; the engine does the constrained decoding.
"""

from __future__ import annotations

import re
from typing import Any

# ── Backend catalog ─────────────────────────────────────────────────
# backend → (relative speed 1-5, requirement, when_to_use)
BACKEND_INFO: dict[str, tuple[int, str, str]] = {
    "xgrammar": (5, "built into vLLM/SGLang/TensorRT-LLM",
                 "Default choice in 2026: fastest (<40µs/token), JSON+grammar."),
    "llguidance": (5, "vLLM (--guided-decoding-backend guidance)",
                   "Rust Earley parser; strong on complex/recursive grammars."),
    "outlines": (4, "vLLM, SGLang (regex→FSM)",
                 "Mature; good for regex/enum/choice constraints."),
    "lm-format-enforcer": (3, "vLLM (token-filtering)",
                           "Simple JSON/regex enforcement; widest model support."),
}

# engine → ordered list of supported backends (best first). The first entry is
# the recommended default for that engine.
ENGINE_BACKENDS: dict[str, list[str]] = {
    "vllm": ["xgrammar", "llguidance", "outlines", "lm-format-enforcer"],
    "sglang": ["xgrammar", "outlines"],
    "tensorrt-llm": ["xgrammar"],
    # Ollama exposes structured output via its own `format` field, not a
    # pluggable backend — handled specially in flag generation.
    "ollama": [],
}

# constraint kind → human description (what the advisor can emit flags for)
CONSTRAINT_KINDS = ("json", "json_schema", "regex", "choice", "grammar")


def list_engines() -> list[str]:
    """Engines aictl can advise guided-decoding for."""
    return list(ENGINE_BACKENDS)


def recommend_backend(engine: str) -> str | None:
    """Best guided-decoding backend for an engine, or None if engine-native."""
    backends = ENGINE_BACKENDS.get(engine.lower())
    if not backends:
        return None
    return backends[0]


def vllm_flags(backend: str = "xgrammar") -> str:
    """vLLM serve flag enabling a guided-decoding backend."""
    return f"--guided-decoding-backend {backend}"


def sglang_flags(backend: str = "xgrammar") -> str:
    """SGLang launch flag selecting a grammar backend."""
    return f"--grammar-backend {backend}"


def request_example(engine: str, schema_kind: str = "json_schema") -> str:
    """A copy-paste OpenAI-style request snippet showing the constraint field."""
    if engine.lower() == "ollama":
        # Ollama uses a top-level `format` field (JSON schema or "json").
        return ('curl http://localhost:11434/api/chat -d \'{\n'
                '  "model": "<model>", "stream": false,\n'
                '  "format": {"type":"object","properties":{...},"required":[...]},\n'
                '  "messages": [{"role":"user","content":"..."}]\n}\'')
    # vLLM / SGLang / TensorRT-LLM share the OpenAI `response_format` /
    # `extra_body` guided fields.
    if schema_kind == "regex":
        body = '"extra_body": {"guided_regex": "\\\\d{4}-\\\\d{2}-\\\\d{2}"}'
    elif schema_kind == "choice":
        body = '"extra_body": {"guided_choice": ["yes", "no", "maybe"]}'
    elif schema_kind == "grammar":
        body = '"extra_body": {"guided_grammar": "root ::= ..."}'
    else:  # json / json_schema
        body = ('"response_format": {"type": "json_schema",\n'
                '    "json_schema": {"name": "out", "schema": { ... }}}')
    return ('client.chat.completions.create(\n'
            '  model="<model>", messages=[...],\n'
            f'  {body})')


# ── Dependency-free JSON-Schema validator (practical subset) ─────────
# Supports the keywords that cover the overwhelming majority of LLM tool /
# structured-output schemas. Not a full Draft-2020-12 implementation (no $ref,
# allOf/anyOf/oneOf, format) — deliberately small and stdlib-only.

_TYPE_PYTHON: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
    "null": type(None),
}


def _type_matches(value: Any, type_name: str) -> bool:
    """Check a value against a single JSON-Schema type name."""
    expected = _TYPE_PYTHON.get(type_name)
    if expected is None:
        return True  # unknown type → don't fail
    # bool is a subclass of int in Python; keep them distinct for JSON Schema.
    if type_name in ("integer", "number") and isinstance(value, bool):
        return False
    if type_name == "boolean":
        return isinstance(value, bool)
    return isinstance(value, expected)


def validate_json_schema(instance: Any, schema: dict[str, Any],
                         _path: str = "$") -> list[str]:
    """Validate ``instance`` against a JSON-Schema subset.

    Returns a list of human-readable error strings (empty == valid). Supported
    keywords: type, enum, const, required, properties, additionalProperties,
    items, minItems/maxItems, minimum/maximum/exclusive*, minLength/maxLength,
    pattern, minProperties/maxProperties.
    """
    errors: list[str] = []

    # type (may be a list of allowed types)
    if "type" in schema:
        types = schema["type"]
        types = types if isinstance(types, list) else [types]
        if not any(_type_matches(instance, t) for t in types):
            errors.append(f"{_path}: expected type {'/'.join(types)}, "
                          f"got {type(instance).__name__}")
            return errors  # type mismatch → skip dependent checks

    # enum / const
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{_path}: {instance!r} not in enum {schema['enum']}")
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{_path}: {instance!r} != const {schema['const']!r}")

    # numbers
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{_path}: {instance} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{_path}: {instance} > maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errors.append(f"{_path}: {instance} <= exclusiveMinimum "
                          f"{schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            errors.append(f"{_path}: {instance} >= exclusiveMaximum "
                          f"{schema['exclusiveMaximum']}")

    # strings
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{_path}: length {len(instance)} < minLength "
                          f"{schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{_path}: length {len(instance)} > maxLength "
                          f"{schema['maxLength']}")
        if "pattern" in schema:
            try:
                if not re.search(schema["pattern"], instance):
                    errors.append(f"{_path}: {instance!r} does not match "
                                  f"pattern {schema['pattern']!r}")
            except re.error:
                pass  # invalid pattern in schema → skip

    # arrays
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{_path}: {len(instance)} items < minItems "
                          f"{schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{_path}: {len(instance)} items > maxItems "
                          f"{schema['maxItems']}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(instance):
                errors.extend(validate_json_schema(item, item_schema,
                                                   f"{_path}[{i}]"))

    # objects
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{_path}: missing required property {key!r}")
        props = schema.get("properties", {})
        for key, subschema in props.items():
            if key in instance and isinstance(subschema, dict):
                errors.extend(validate_json_schema(instance[key], subschema,
                                                   f"{_path}.{key}"))
        if schema.get("additionalProperties") is False:
            extra = [k for k in instance if k not in props]
            if extra:
                errors.append(f"{_path}: additional properties not allowed: "
                              f"{', '.join(sorted(extra))}")
        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            errors.append(f"{_path}: {len(instance)} properties < minProperties "
                          f"{schema['minProperties']}")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            errors.append(f"{_path}: {len(instance)} properties > maxProperties "
                          f"{schema['maxProperties']}")

    return errors


def is_valid(instance: Any, schema: dict[str, Any]) -> bool:
    """True if ``instance`` satisfies ``schema`` (subset validator)."""
    return not validate_json_schema(instance, schema)

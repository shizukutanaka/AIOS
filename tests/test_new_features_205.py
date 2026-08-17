"""Pass 205: design review for constrained-decoding JSON Schemas.

`guided validate` answered "does this document match this schema?". Nothing
answered the prior question: "is this schema one a model can fill in well?"

That gap matters because grammar-constrained generation gives a **format
guarantee, not a semantic one**. A schema can be perfectly valid, compile fine
in XGrammar, and produce parseable output on every request while making the
answers worse — and the failure is invisible precisely because the structural
check passes.

The load-bearing check is field ordering. Generation is autoregressive, so a
schema emitting `answer` before `reasoning` forces the model to commit to a
conclusion and then rationalize it; the model cannot think first if the
grammar will not let it write first. Both `properties` order and `required`
order are checked, since backends differ in which they emit by.

Everything here is advisory. Good schemas break these rules deliberately, and
a linter that blocks deployment on a heuristic is worse than one that explains
itself — so `lint_schema` never raises and the command only exits non-zero
under `--strict`.
"""

from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout

from aictl.cmd.guided import run_lint
from aictl.runtime.schema_lint import (
    MAX_FIELD_COUNT,
    MAX_NESTING_DEPTH,
    lint_schema,
)


def _rules(schema):
    return [f.rule for f in lint_schema(schema)]


class TestReasoningOrder(unittest.TestCase):
    """The check that catches an otherwise invisible quality bug."""

    def test_answer_before_reasoning_is_flagged(self):
        schema = {"type": "object", "properties": {
            "answer": {"type": "string"}, "reasoning": {"type": "string"}}}
        self.assertIn("answer_before_reasoning", _rules(schema))

    def test_reasoning_before_answer_is_clean(self):
        schema = {"type": "object", "properties": {
            "reasoning": {"type": "string", "description": "r"},
            "answer": {"type": "string", "description": "a"}},
            "required": ["reasoning", "answer"]}
        self.assertNotIn("answer_before_reasoning", _rules(schema))

    def test_required_order_is_checked_separately(self):
        # Some backends emit in `required` order rather than `properties`
        # order, so a schema can be correct in one and wrong in the other.
        schema = {"type": "object",
                  "properties": {"reasoning": {"type": "string", "description": "r"},
                                 "answer": {"type": "string", "description": "a"}},
                  "required": ["answer", "reasoning"]}
        findings = [f for f in lint_schema(schema)
                    if f.rule == "answer_before_reasoning"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].path, "required")

    def test_synonyms_are_recognized(self):
        for answer_key, reason_key in (("verdict", "rationale"),
                                       ("conclusion", "analysis"),
                                       ("label", "explanation")):
            schema = {"type": "object", "properties": {
                answer_key: {"type": "string"}, reason_key: {"type": "string"}}}
            self.assertIn("answer_before_reasoning", _rules(schema),
                          f"{answer_key}/{reason_key}")

    def test_answer_alone_is_not_flagged(self):
        # Nothing to reorder if there is no reasoning field at all.
        schema = {"type": "object", "properties": {
            "answer": {"type": "string", "description": "a"}},
            "required": ["answer"]}
        self.assertNotIn("answer_before_reasoning", _rules(schema))

    def test_reasoning_alone_is_not_flagged(self):
        schema = {"type": "object", "properties": {
            "reasoning": {"type": "string", "description": "r"}},
            "required": ["reasoning"]}
        self.assertNotIn("answer_before_reasoning", _rules(schema))

    def test_unrelated_field_names_are_not_flagged(self):
        # A false positive tells someone their schema is wrong when it is not,
        # which is worse than staying quiet.
        schema = {"type": "object", "properties": {
            "name": {"type": "string", "description": "n"},
            "address": {"type": "string", "description": "a"}},
            "required": ["name", "address"]}
        self.assertNotIn("answer_before_reasoning", _rules(schema))

    def test_message_names_both_fields(self):
        schema = {"type": "object", "properties": {
            "answer": {"type": "string"}, "reasoning": {"type": "string"}}}
        message = next(f.message for f in lint_schema(schema)
                       if f.rule == "answer_before_reasoning")
        self.assertIn("answer", message)
        self.assertIn("reasoning", message)


class TestStructuralChecks(unittest.TestCase):
    def test_deep_nesting_is_flagged(self):
        node = {"type": "string", "description": "leaf"}
        for _ in range(MAX_NESTING_DEPTH):
            node = {"type": "object", "description": "d", "properties": {"n": node}}
        self.assertIn("deep_nesting", _rules(node))

    def test_shallow_schema_is_not_flagged(self):
        schema = {"type": "object", "properties": {
            "a": {"type": "string", "description": "d"}}, "required": ["a"]}
        self.assertNotIn("deep_nesting", _rules(schema))

    def test_wide_schema_is_flagged(self):
        props = {f"f{i}": {"type": "string", "description": "d"}
                 for i in range(MAX_FIELD_COUNT)}
        schema = {"type": "object", "properties": props,
                  "required": list(props)}
        self.assertIn("too_many_fields", _rules(schema))

    def test_arrays_are_traversed(self):
        # A deep structure hidden behind array items must still be seen.
        leaf = {"type": "string", "description": "leaf"}
        schema = {"type": "object", "properties": {
            "items": {"type": "array", "items": {
                "type": "object", "properties": {
                    "inner": {"type": "object", "properties": {
                        "deeper": {"type": "object", "properties": {"x": leaf}}}}}}}}}
        self.assertIn("deep_nesting", _rules(schema))


class TestDescriptionAndNullChecks(unittest.TestCase):
    def test_missing_description_is_info_not_warn(self):
        schema = {"type": "object", "properties": {"a": {"type": "string"}},
                  "required": ["a"]}
        finding = next(f for f in lint_schema(schema)
                       if f.rule == "missing_descriptions")
        self.assertEqual(finding.severity, "info")

    def test_described_fields_are_clean(self):
        schema = {"type": "object", "properties": {
            "a": {"type": "string", "description": "what a means"}},
            "required": ["a"]}
        self.assertNotIn("missing_descriptions", _rules(schema))

    def test_blank_description_counts_as_missing(self):
        schema = {"type": "object", "properties": {
            "a": {"type": "string", "description": "   "}}, "required": ["a"]}
        self.assertIn("missing_descriptions", _rules(schema))

    def test_optional_field_without_null_is_flagged(self):
        schema = {"type": "object", "properties": {
            "maybe": {"type": "string", "description": "d"}}, "required": []}
        self.assertIn("optional_without_null", _rules(schema))

    def test_optional_field_allowing_null_is_clean(self):
        schema = {"type": "object", "properties": {
            "maybe": {"type": ["string", "null"], "description": "d"}},
            "required": []}
        self.assertNotIn("optional_without_null", _rules(schema))

    def test_required_field_without_null_is_clean(self):
        # A required field is meant to always be present; null is not implied.
        schema = {"type": "object", "properties": {
            "must": {"type": "string", "description": "d"}}, "required": ["must"]}
        self.assertNotIn("optional_without_null", _rules(schema))

    def test_anyof_is_treated_as_deliberate(self):
        schema = {"type": "object", "properties": {
            "maybe": {"anyOf": [{"type": "string"}, {"type": "null"}],
                      "description": "d"}}, "required": []}
        self.assertNotIn("optional_without_null", _rules(schema))


class TestRobustness(unittest.TestCase):
    def test_non_object_schema_yields_a_finding_not_an_exception(self):
        self.assertEqual(_rules(["not", "a", "schema"]), ["not_an_object"])
        self.assertEqual(_rules("string"), ["not_an_object"])

    def test_empty_schema_is_clean(self):
        self.assertEqual(_rules({}), [])

    def test_schema_without_properties_is_clean(self):
        self.assertEqual(_rules({"type": "string"}), [])

    def test_malformed_required_does_not_crash(self):
        schema = {"type": "object",
                  "properties": {"a": {"type": "string", "description": "d"}},
                  "required": [None, 3, {"x": 1}]}
        lint_schema(schema)   # must not raise

    def test_findings_serialize(self):
        schema = {"type": "object", "properties": {
            "answer": {"type": "string"}, "reasoning": {"type": "string"}}}
        payload = json.loads(json.dumps([f.to_dict() for f in lint_schema(schema)]))
        self.assertEqual(sorted(payload[0].keys()),
                         ["message", "path", "rule", "severity"])


class TestCli(unittest.TestCase):
    def _lint(self, schema, use_json=False, strict=False):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(schema, fh)
            path = fh.name
        namespace = argparse.Namespace(schema=path, strict=strict, json=use_json)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = run_lint(namespace)
        output = buffer.getvalue()
        return code, (json.loads(output) if use_json else output)

    def test_clean_schema_reports_nothing(self):
        code, output = self._lint({"type": "object", "properties": {
            "reasoning": {"type": "string", "description": "r"},
            "answer": {"type": "string", "description": "a"}},
            "required": ["reasoning", "answer"]})
        self.assertEqual(code, 0)
        self.assertIn("No design problems", output)

    def test_bad_schema_explains_itself(self):
        code, output = self._lint({"type": "object", "properties": {
            "answer": {"type": "string"}, "reasoning": {"type": "string"}}})
        self.assertEqual(code, 0)          # advisory by default
        self.assertIn("autoregressive", output)

    def test_strict_exits_nonzero_on_a_warning(self):
        code, _ = self._lint({"type": "object", "properties": {
            "answer": {"type": "string"}, "reasoning": {"type": "string"}}},
            strict=True)
        self.assertEqual(code, 1)

    def test_strict_ignores_info_only_findings(self):
        # Info findings are observations, not problems worth failing a build.
        code, _ = self._lint({"type": "object", "properties": {
            "a": {"type": "string"}}, "required": ["a"]}, strict=True)
        self.assertEqual(code, 0)

    def test_json_output_shape(self):
        code, payload = self._lint({"type": "object", "properties": {
            "answer": {"type": "string"}, "reasoning": {"type": "string"}}},
            use_json=True)
        self.assertEqual(code, 0)
        self.assertIn("findings", payload)
        self.assertGreaterEqual(payload["warnings"], 1)

    def test_unreadable_schema_errors_cleanly(self):
        namespace = argparse.Namespace(schema="/nonexistent/schema.json",
                                       strict=False, json=False)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = run_lint(namespace)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()

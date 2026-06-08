"""Tests for the guided/structured-decoding advisor + JSON-Schema validator."""

from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.runtime.guided import (
    ENGINE_BACKENDS, BACKEND_INFO, recommend_backend, vllm_flags,
    sglang_flags, validate_json_schema, is_valid,
)
from aictl.cmd import guided


class TestBackendMatrix(unittest.TestCase):
    def test_every_engine_recommends_a_listed_backend(self):
        for engine, backends in ENGINE_BACKENDS.items():
            rec = recommend_backend(engine)
            if backends:
                self.assertEqual(rec, backends[0])
                self.assertIn(rec, BACKEND_INFO)
            else:
                self.assertIsNone(rec)  # engine-native (ollama)

    def test_xgrammar_is_vllm_default(self):
        self.assertEqual(recommend_backend("vllm"), "xgrammar")
        self.assertEqual(recommend_backend("sglang"), "xgrammar")
        self.assertEqual(recommend_backend("tensorrt-llm"), "xgrammar")

    def test_flag_helpers(self):
        self.assertIn("xgrammar", vllm_flags("xgrammar"))
        self.assertIn("guided-decoding-backend", vllm_flags())
        self.assertIn("grammar-backend", sglang_flags())

    def test_unknown_engine_returns_none(self):
        self.assertIsNone(recommend_backend("nonexistent-engine"))


class TestJsonSchemaValidator(unittest.TestCase):
    def test_valid_object(self):
        schema = {"type": "object",
                  "properties": {"name": {"type": "string"},
                                 "age": {"type": "integer"}},
                  "required": ["name"]}
        self.assertTrue(is_valid({"name": "x", "age": 3}, schema))
        self.assertEqual(validate_json_schema({"name": "x"}, schema), [])

    def test_missing_required(self):
        schema = {"type": "object", "required": ["name"]}
        errors = validate_json_schema({}, schema)
        self.assertEqual(len(errors), 1)
        self.assertIn("required", errors[0])

    def test_type_mismatch(self):
        errors = validate_json_schema("hello", {"type": "integer"})
        self.assertTrue(errors)
        self.assertIn("expected type", errors[0])

    def test_bool_is_not_integer(self):
        # JSON Schema: booleans are not integers even though bool ⊂ int in Python
        self.assertFalse(is_valid(True, {"type": "integer"}))
        self.assertTrue(is_valid(True, {"type": "boolean"}))

    def test_enum_and_const(self):
        self.assertFalse(is_valid("red", {"enum": ["green", "blue"]}))
        self.assertTrue(is_valid("blue", {"enum": ["green", "blue"]}))
        self.assertFalse(is_valid(5, {"const": 7}))

    def test_numeric_bounds(self):
        s = {"type": "number", "minimum": 0, "maximum": 10}
        self.assertTrue(is_valid(5, s))
        self.assertFalse(is_valid(-1, s))
        self.assertFalse(is_valid(11, s))
        self.assertTrue(is_valid(0, {"exclusiveMinimum": 0}) is False)

    def test_string_length_and_pattern(self):
        s = {"type": "string", "minLength": 2, "maxLength": 4,
             "pattern": "^[a-z]+$"}
        self.assertTrue(is_valid("abc", s))
        self.assertFalse(is_valid("a", s))       # too short
        self.assertFalse(is_valid("abcde", s))   # too long
        self.assertFalse(is_valid("AB", s))      # pattern fail (also too short ok len2)

    def test_array_items_and_bounds(self):
        s = {"type": "array", "items": {"type": "integer"},
             "minItems": 1, "maxItems": 3}
        self.assertTrue(is_valid([1, 2], s))
        self.assertFalse(is_valid([], s))             # below minItems
        self.assertFalse(is_valid([1, 2, 3, 4], s))   # above maxItems
        self.assertFalse(is_valid([1, "x"], s))       # item type mismatch

    def test_nested_object_path_in_error(self):
        schema = {"type": "object", "properties": {
            "user": {"type": "object", "properties": {
                "age": {"type": "integer"}}}}}
        errors = validate_json_schema({"user": {"age": "old"}}, schema)
        self.assertTrue(any("$.user.age" in e for e in errors))

    def test_additional_properties_false(self):
        schema = {"type": "object", "properties": {"a": {"type": "string"}},
                  "additionalProperties": False}
        self.assertTrue(is_valid({"a": "x"}, schema))
        errors = validate_json_schema({"a": "x", "b": 1}, schema)
        self.assertTrue(any("additional" in e for e in errors))


class TestGuidedCLI(unittest.TestCase):
    def _run(self, func, **ns):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = func(argparse.Namespace(**ns))
        return rc, buf.getvalue()

    def test_recommend_vllm_json(self):
        rc, out = self._run(guided.run_recommend, engine="vllm",
                            kind="json_schema", json=True)
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["backend"], "xgrammar")
        self.assertIn("guided-decoding-backend", data["serve_flags"])

    def test_recommend_ollama_is_native(self):
        rc, out = self._run(guided.run_recommend, engine="ollama",
                            kind="json_schema", json=True)
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["native"])
        self.assertIsNone(data["backend"])

    def test_recommend_unknown_engine(self):
        rc, _ = self._run(guided.run_recommend, engine="bogus",
                          kind="json_schema", json=False)
        self.assertEqual(rc, 1)

    def test_matrix_json_shape(self):
        rc, out = self._run(guided.run_matrix, json=True)
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIn("matrix", data)
        engines = {row["engine"] for row in data["matrix"]}
        self.assertIn("vllm", engines)
        self.assertIn("ollama", engines)

    def test_validate_command_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_p = Path(tmp) / "d.json"
            schema_p = Path(tmp) / "s.json"
            data_p.write_text(json.dumps({"name": "x"}))
            schema_p.write_text(json.dumps(
                {"type": "object", "required": ["name"]}))
            rc, out = self._run(guided.run_validate, data=str(data_p),
                                schema=str(schema_p), json=True)
            self.assertEqual(rc, 0)
            self.assertTrue(json.loads(out)["valid"])

    def test_validate_command_invalid_returns_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_p = Path(tmp) / "d.json"
            schema_p = Path(tmp) / "s.json"
            data_p.write_text(json.dumps({}))
            schema_p.write_text(json.dumps(
                {"type": "object", "required": ["name"]}))
            rc, out = self._run(guided.run_validate, data=str(data_p),
                                schema=str(schema_p), json=True)
            self.assertEqual(rc, 2)
            self.assertFalse(json.loads(out)["valid"])


class TestGuidedRegistration(unittest.TestCase):
    def test_registered_in_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        import argparse as _a
        top = next(a for a in p._actions
                   if isinstance(a, _a._SubParsersAction))
        self.assertIn("guided", top.choices)

    def test_subcommands_accept_json(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        self.assertTrue(p.parse_args(["guided", "recommend", "--json"]).json)
        self.assertTrue(p.parse_args(["--json", "guided", "matrix"]).json)


if __name__ == "__main__":
    unittest.main()

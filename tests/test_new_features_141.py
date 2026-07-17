"""Pass 141: MCP numeric tool args must tolerate JSON-string numbers.

MCP tool calls are frequently LLM-generated, where a numeric field commonly
arrives as a JSON *string* (``"10"``) rather than a JSON number. The server read
those with a bare ``args.get(key, default)`` and passed them through unchanged,
so the underlying function blew up with an opaque internal error — e.g.
``aictl_recommend`` with ``max_results="10"`` reached recommend()'s
``if max_results <= 0`` guard and raised
``TypeError: '<=' not supported between instances of 'str' and 'int'``, surfaced
to the MCP client as a cryptic "Error: ...".

Fix: coerce numeric arguments at the MCP boundary with `_int_arg`/`_float_arg`,
which accept ints, floats, and numeric strings, reject genuinely non-numeric
input (and JSON booleans) with a clear message, and preserve the default when the
key is absent/None. Applied to max_results (recommend), gpu_count/model_size_b
(optimize), and period_days (tco).
"""

from __future__ import annotations

import json
import unittest


class TestNumArgHelpers(unittest.TestCase):
    def test_int_arg_coerces_string(self):
        from aictl.mcp_server import _int_arg
        self.assertEqual(_int_arg({"n": "10"}, "n", 5), 10)

    def test_int_arg_passthrough_int(self):
        from aictl.mcp_server import _int_arg
        self.assertEqual(_int_arg({"n": 7}, "n", 5), 7)

    def test_int_arg_float_truncates(self):
        from aictl.mcp_server import _int_arg
        self.assertEqual(_int_arg({"n": 3.9}, "n", 5), 3)

    def test_int_arg_default_when_absent_or_none(self):
        from aictl.mcp_server import _int_arg
        self.assertEqual(_int_arg({}, "n", 5), 5)
        self.assertEqual(_int_arg({"n": None}, "n", 5), 5)

    def test_int_arg_rejects_non_numeric(self):
        from aictl.mcp_server import _int_arg
        with self.assertRaises(ValueError):
            _int_arg({"n": "abc"}, "n", 5)

    def test_int_arg_rejects_bool(self):
        from aictl.mcp_server import _int_arg
        with self.assertRaises(ValueError):
            _int_arg({"n": True}, "n", 5)

    def test_float_arg_coerces_string(self):
        from aictl.mcp_server import _float_arg
        self.assertEqual(_float_arg({"x": "7.5"}, "x", None), 7.5)

    def test_float_arg_default_and_reject(self):
        from aictl.mcp_server import _float_arg
        self.assertIsNone(_float_arg({}, "x", None))
        with self.assertRaises(ValueError):
            _float_arg({"x": "NaNaN"}, "x", None)


class TestMcpToolsTolerateStringNumbers(unittest.TestCase):
    def test_recommend_string_max_results(self):
        from aictl.mcp_server import handle_tool
        res = handle_tool("aictl_recommend", {"max_results": "3"})
        self.assertNotEqual(res.get("isError"), True)
        recs = json.loads(res["content"][0]["text"])
        self.assertLessEqual(len(recs), 3)

    def test_recommend_invalid_max_results_clean_error(self):
        from aictl.mcp_server import handle_tool
        res = handle_tool("aictl_recommend", {"max_results": "abc"})
        self.assertTrue(res.get("isError"))
        self.assertIn("max_results", res["content"][0]["text"])

    def test_optimize_string_numbers(self):
        from aictl.mcp_server import handle_tool
        res = handle_tool("aictl_optimize", {
            "gpu": "H100", "gpu_count": "2",
            "model": "llama", "model_size_b": "7"})
        self.assertNotEqual(res.get("isError"), True)

    def test_tco_string_period_days(self):
        from aictl.mcp_server import handle_tool
        res = handle_tool("aictl_tco", {"period_days": "30"})
        self.assertNotEqual(res.get("isError"), True)


if __name__ == "__main__":
    unittest.main()

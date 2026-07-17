"""Pass 15 regression tests for correctness bugs identified by deep audit."""

import unittest


class TestMcpQuantValidatesModel(unittest.TestCase):
    """mcp_server.py: _tool_quant must validate the schema-required 'model' param."""

    def test_empty_model_returns_error(self):
        from aictl.mcp_server import _tool_quant
        result = _tool_quant({})
        self.assertTrue(
            result.get("isError"),
            "_tool_quant must return isError when required 'model' is missing "
            "(the tool schema declares 'model' as required)",
        )
        text = result["content"][0]["text"].lower()
        self.assertIn("model", text)

    def test_valid_model_returns_table(self):
        from aictl.mcp_server import _tool_quant
        result = _tool_quant({"model": "llama3.1:8b"})
        self.assertFalse(result.get("isError", False))
        text = result["content"][0]["text"]
        self.assertIn("llama3.1:8b", text)

    def test_schema_requires_model(self):
        from aictl.mcp_server import TOOLS
        quant = next((t for t in TOOLS if t["name"] == "aictl_quant"), None)
        self.assertIsNotNone(quant, "aictl_quant tool must exist")
        self.assertIn(
            "model",
            quant["inputSchema"].get("required", []),
            "aictl_quant schema must declare 'model' as required",
        )


class TestSdkNoDeadOutAssignment(unittest.TestCase):
    """sdk.py: cloud-fallback path must return the tuple directly, no dead intermediate."""

    def test_no_intermediate_out_tuple(self):
        import pathlib, re
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "sdk.py").read_text()
        # The dead pattern was: out: tuple[str, int] = (text, tokens); return out
        dead = re.search(r"out:\s*tuple\[str,\s*int\]\s*=\s*\(text,\s*tokens\)", src)
        self.assertIsNone(
            dead,
            "sdk.py must not assign an intermediate 'out' tuple just to return it",
        )


if __name__ == "__main__":
    unittest.main()

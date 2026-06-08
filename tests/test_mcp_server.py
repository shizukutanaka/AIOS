"""Tests for MCP Server — verify all tools work."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aictl.mcp_server as _mcp
from aictl.mcp_server import handle_tool, handle_request, TOOLS, get_tool_spans


class TestMCPTools(unittest.TestCase):
    def test_all_tools_defined(self):
        names = [t["name"] for t in TOOLS]
        expected = ["aictl_health", "aictl_recommend", "aictl_cost",
                    "aictl_optimize", "aictl_security", "aictl_status",
                    "aictl_recipes", "aictl_meter", "aictl_lora", "aictl_fabric"]
        for e in expected:
            self.assertIn(e, names, f"Missing tool: {e}")

    def test_tool_schemas_valid(self):
        for tool in TOOLS:
            self.assertIn("name", tool)
            self.assertIn("description", tool)
            self.assertIn("inputSchema", tool)
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_mcp_health_endpoint(self):
        result = handle_tool("aictl_health", {})
        text = result["content"][0]["text"]
        data = json.loads(text)
        self.assertIn("profile", data)

    def test_recommend(self):
        result = handle_tool("aictl_recommend", {"max_results": 3})
        text = result["content"][0]["text"]
        data = json.loads(text)
        # MCP returns list of recommendations or dict with recommendations key
        if isinstance(data, list):
            self.assertGreater(len(data), 0)
        else:
            self.assertIn("recommendations", data)

    def test_mcp_cost_endpoint(self):
        result = handle_tool("aictl_cost", {})
        text = result["content"][0]["text"]
        data = json.loads(text)
        # MCP returns list of GPU cost dicts or dict with gpus key
        if isinstance(data, list):
            self.assertGreater(len(data), 5)
        else:
            self.assertIn("gpus", data)

    def test_optimize(self):
        result = handle_tool("aictl_optimize", {
            "model": "llama3", "model_size_b": 8.0, "gpu": "H100",
        })
        text = result["content"][0]["text"]
        data = json.loads(text)
        self.assertIn("command", data)
        self.assertIn("--kv-cache-dtype=fp8", data["command"])

    def test_security(self):
        result = handle_tool("aictl_security", {})
        text = result["content"][0]["text"]
        data = json.loads(text)
        self.assertIn("score", data)

    def test_mcp_status_endpoint(self):
        result = handle_tool("aictl_status", {})
        text = result["content"][0]["text"]
        data = json.loads(text)
        self.assertIn("hostname", data)

    def test_recipes(self):
        result = handle_tool("aictl_recipes", {})
        text = result["content"][0]["text"]
        data = json.loads(text)
        self.assertGreaterEqual(len(data), 10)

    def test_mcp_meter_endpoint(self):
        result = handle_tool("aictl_meter", {})
        text = result["content"][0]["text"]
        data = json.loads(text)
        self.assertIn("total_tokens", data)

    def test_mcp_lora_endpoint(self):
        result = handle_tool("aictl_lora", {})
        text = result["content"][0]["text"]
        data = json.loads(text)
        self.assertIsInstance(data, list)

    def test_mcp_fabric_endpoint(self):
        result = handle_tool("aictl_fabric", {})
        text = result["content"][0]["text"]
        data = json.loads(text)
        self.assertIn("tiers", data)
        self.assertGreater(data["total_gb"], 0)

    def test_unknown_tool(self):
        result = handle_tool("nonexistent", {})
        self.assertTrue(result.get("isError", False))


class TestMCPProtocol(unittest.TestCase):
    def test_initialize(self):
        resp = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(resp["id"], 1)
        self.assertIn("protocolVersion", resp["result"])
        self.assertIn("capabilities", resp["result"])

    def test_tools_list(self):
        resp = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = resp["result"]["tools"]
        self.assertEqual(len(tools), 18)

    def test_tools_call(self):
        resp = handle_request({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "aictl_status", "arguments": {}},
        })
        self.assertIn("content", resp["result"])


class TestMCPToolSpans(unittest.TestCase):
    """OTel span instrumentation for MCP tool calls."""

    def setUp(self):
        # Clear the ring buffer before each test.
        _mcp._TOOL_SPANS.clear()

    def test_span_recorded_on_success(self):
        handle_tool("aictl_health", {})
        spans = get_tool_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].tool_name, "aictl_health")
        self.assertTrue(spans[0].success)
        self.assertEqual(spans[0].error, "")

    def test_span_records_error_flag(self):
        handle_tool("nonexistent_tool_xyz", {})
        spans = get_tool_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].tool_name, "nonexistent_tool_xyz")
        self.assertFalse(spans[0].success)
        self.assertNotEqual(spans[0].error, "")

    def test_span_has_positive_duration(self):
        handle_tool("aictl_status", {})
        spans = get_tool_spans()
        self.assertGreater(spans[0].duration_ms(), 0.0)

    def test_ring_bounded_at_200(self):
        from aictl.metrics.genai_spans import ToolSpan
        for i in range(210):
            _mcp._TOOL_SPANS.append(ToolSpan(tool_name=f"t{i}"))
        # deque(maxlen=200) silently drops oldest; never exceeds 200.
        self.assertEqual(len(_mcp._TOOL_SPANS), 200)
        # Oldest entries are gone; only the last 200 survive.
        self.assertEqual(_mcp._TOOL_SPANS[-1].tool_name, "t209")

    def test_otel_attributes_shape(self):
        from aictl.metrics.genai_spans import ToolSpan
        span = ToolSpan(tool_name="aictl_health", success=True,
                        start_time_ns=1_000_000, end_time_ns=2_000_000)
        attrs = span.to_otel_attributes()
        self.assertEqual(attrs["gen_ai.operation.name"], "tool")
        self.assertEqual(attrs["aios.mcp.tool_name"], "aictl_health")
        self.assertTrue(attrs["aios.mcp.success"])
        self.assertAlmostEqual(attrs["aios.mcp.duration_ms"], 1.0, places=3)
        self.assertNotIn("aios.mcp.error", attrs)

    def test_otel_attributes_include_error_when_failed(self):
        from aictl.metrics.genai_spans import ToolSpan
        span = ToolSpan(tool_name="bad_tool", success=False,
                        start_time_ns=1_000_000, end_time_ns=3_000_000,
                        error="Unknown tool: bad_tool")
        attrs = span.to_otel_attributes()
        self.assertFalse(attrs["aios.mcp.success"])
        self.assertIn("aios.mcp.error", attrs)
        self.assertIn("bad_tool", attrs["aios.mcp.error"])

    def test_otlp_span_structure(self):
        from aictl.metrics.genai_spans import ToolSpan
        span = ToolSpan(tool_name="aictl_cost", success=True,
                        start_time_ns=5_000_000_000, end_time_ns=5_001_000_000)
        otlp = span.to_otlp_span()
        self.assertEqual(otlp["name"], "mcp aictl_cost")
        self.assertEqual(otlp["kind"], 3)
        self.assertEqual(otlp["status"]["code"], 1)
        keys = {a["key"] for a in otlp["attributes"]}
        self.assertIn("gen_ai.operation.name", keys)
        self.assertIn("aios.mcp.tool_name", keys)

    def test_mcp_ping_endpoint(self):
        resp = handle_request({"jsonrpc": "2.0", "id": 4, "method": "ping"})
        self.assertEqual(resp["result"], {})

    def test_unknown_method(self):
        resp = handle_request({"jsonrpc": "2.0", "id": 5, "method": "unknown"})
        self.assertIn("error", resp)

    def test_initialized_notification(self):
        resp = handle_request({"jsonrpc": "2.0", "method": "initialized"})
        self.assertIsNone(resp)


if __name__ == "__main__":
    unittest.main()

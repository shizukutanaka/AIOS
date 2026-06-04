"""Tests for MCP Server — verify all tools work."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.mcp_server import handle_tool, handle_request, TOOLS


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

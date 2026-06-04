"""Tests for MCP (Model Context Protocol) server."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.mcp_server import handle_request, handle_tool, TOOLS


class TestMCPProtocol(unittest.TestCase):
    def test_initialize(self):
        resp = handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05",
                       "clientInfo": {"name": "test", "version": "1.0"}},
        })
        self.assertEqual(resp["id"], 1)
        self.assertEqual(resp["result"]["protocolVersion"], "2024-11-05")
        self.assertIn("tools", resp["result"]["capabilities"])

    def test_initialized_no_response(self):
        resp = handle_request({
            "jsonrpc": "2.0", "method": "initialized",
        })
        self.assertIsNone(resp)

    def test_tools_list(self):
        resp = handle_request({
            "jsonrpc": "2.0", "id": 2, "method": "tools/list",
        })
        tools = resp["result"]["tools"]
        self.assertGreaterEqual(len(tools), 7)
        names = [t["name"] for t in tools]
        self.assertIn("aictl_health", names)
        self.assertIn("aictl_recommend", names)
        self.assertIn("aictl_cost", names)
        self.assertIn("aictl_optimize", names)

    def test_mcp_ping_responds(self):
        resp = handle_request({
            "jsonrpc": "2.0", "id": 3, "method": "ping",
        })
        self.assertEqual(resp["id"], 3)

    def test_unknown_method(self):
        resp = handle_request({
            "jsonrpc": "2.0", "id": 4, "method": "nonexistent",
        })
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32601)


class TestMCPTools(unittest.TestCase):
    def test_mcp_health_responds(self):
        result = handle_tool("aictl_health", {})
        self.assertIn("content", result)
        data = json.loads(result["content"][0]["text"])
        self.assertIn("profile", data)
        self.assertIn("security_score", data)

    def test_recommend(self):
        result = handle_tool("aictl_recommend", {"max_results": 3})
        data = json.loads(result["content"][0]["text"])
        self.assertIsInstance(data, list)
        # Should find models for current hardware (9GB RAM)
        self.assertGreater(len(data), 0)
        self.assertIn("name", data[0])

    def test_recommend_code(self):
        result = handle_tool("aictl_recommend", {"use_case": "code", "max_results": 2})
        data = json.loads(result["content"][0]["text"])
        for m in data:
            self.assertEqual(m["use_case"], "code")

    def test_mcp_cost_responds(self):
        result = handle_tool("aictl_cost", {})
        data = json.loads(result["content"][0]["text"])
        self.assertGreater(len(data), 5)
        gpu_names = [d["gpu"] for d in data]
        self.assertIn("B200", gpu_names)

    def test_optimize(self):
        result = handle_tool("aictl_optimize", {
            "model": "meta-llama/Llama-3.1-8B-Instruct",
            "model_size_b": 8.0,
            "gpu": "H100",
        })
        data = json.loads(result["content"][0]["text"])
        self.assertIn("command", data)
        self.assertIn("vllm serve", data["command"])
        self.assertIn("--kv-cache-dtype=fp8", data["flags"])

    def test_security(self):
        result = handle_tool("aictl_security", {})
        data = json.loads(result["content"][0]["text"])
        self.assertIn("score", data)
        self.assertIsInstance(data["score"], int)

    def test_mcp_status_responds(self):
        result = handle_tool("aictl_status", {})
        data = json.loads(result["content"][0]["text"])
        self.assertIn("hostname", data)
        self.assertIn("profile", data)

    def test_recipes(self):
        result = handle_tool("aictl_recipes", {})
        data = json.loads(result["content"][0]["text"])
        self.assertGreaterEqual(len(data), 10)

    def test_unknown_tool(self):
        result = handle_tool("nonexistent", {})
        self.assertTrue(result.get("isError", False))


class TestMCPToolsCall(unittest.TestCase):
    def test_tools_call_via_rpc(self):
        resp = handle_request({
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {"name": "aictl_status", "arguments": {}},
        })
        self.assertEqual(resp["id"], 10)
        data = json.loads(resp["result"]["content"][0]["text"])
        self.assertIn("hostname", data)

    def test_tools_call_optimize_via_rpc(self):
        resp = handle_request({
            "jsonrpc": "2.0", "id": 11, "method": "tools/call",
            "params": {
                "name": "aictl_optimize",
                "arguments": {"model": "llama3", "model_size_b": 8.0, "gpu": "B200"},
            },
        })
        data = json.loads(resp["result"]["content"][0]["text"])
        self.assertIn("--dtype=fp8", data["flags"])  # Blackwell gets FP8 weights


class TestToolSchemas(unittest.TestCase):
    def test_all_tools_have_schemas(self):
        for tool in TOOLS:
            self.assertIn("name", tool)
            self.assertIn("description", tool)
            self.assertIn("inputSchema", tool)
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_optimize_has_required(self):
        opt = [t for t in TOOLS if t["name"] == "aictl_optimize"][0]
        self.assertIn("model", opt["inputSchema"]["required"])
        self.assertIn("model_size_b", opt["inputSchema"]["required"])


if __name__ == "__main__":
    unittest.main()

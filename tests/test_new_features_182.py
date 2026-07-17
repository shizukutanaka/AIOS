"""Pass 182 (IMPROVEMENTS.md item O): MCP 2026-07-28 release-candidate
compatibility.

The RC (final ships 2026-07-28) removes the initialize handshake in favor
of a stateless core, adds `server/discover`, and adds `ttlMs`/`cacheScope`
to list responses. aictl/mcp_server.py was already internally stateless (no
session tracking anywhere), so full compatibility is cheap: this pass adds
dual-mode support rather than a breaking rewrite.

- `initialize` is KEPT (legacy clients still send it) but now negotiates
  the protocol version: a client's requested protocolVersion is echoed back
  verbatim if this server speaks it (SUPPORTED_MCP_VERSIONS); otherwise the
  default (2024-11-05, unchanged from before this pass) is returned. A
  client that omits protocolVersion entirely also gets the unchanged
  default -- zero behavior change for every pre-existing caller.
- `server/discover` is NEW: the RC's stateless replacement for initialize.
  Callable with no prior handshake since this server never required one in
  practice.
- `_meta` in params is tolerated implicitly (params.get(...) only reads
  known keys; an unrecognized _meta key is simply ignored, not new code --
  pinned here as a regression guard).
- `tools/list` gains `ttlMs`/`cacheScope` fields (TOOLS is static per
  server process, so a long, server-scoped TTL is accurate).
"""

from __future__ import annotations

import unittest


class TestVersionNegotiation(unittest.TestCase):
    def test_no_protocol_version_gets_default(self):
        from aictl.mcp_server import handle_request, MCP_PROTOCOL_VERSION
        resp = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(resp["result"]["protocolVersion"], MCP_PROTOCOL_VERSION)

    def test_legacy_version_request_echoed_back(self):
        # Pins the exact pre-existing behavior test_mcp.py already checks:
        # a client explicitly requesting 2024-11-05 must still get exactly
        # that back, unchanged by this pass.
        from aictl.mcp_server import handle_request
        resp = handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        })
        self.assertEqual(resp["result"]["protocolVersion"], "2024-11-05")

    def test_2025_06_18_request_echoed_back(self):
        from aictl.mcp_server import handle_request
        resp = handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        })
        self.assertEqual(resp["result"]["protocolVersion"], "2025-06-18")

    def test_2026_07_28_rc_request_echoed_back(self):
        from aictl.mcp_server import handle_request
        resp = handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2026-07-28"},
        })
        self.assertEqual(resp["result"]["protocolVersion"], "2026-07-28")

    def test_unsupported_version_falls_back_to_default(self):
        from aictl.mcp_server import handle_request, MCP_PROTOCOL_VERSION
        resp = handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "1999-01-01"},
        })
        self.assertEqual(resp["result"]["protocolVersion"], MCP_PROTOCOL_VERSION)

    def test_initialize_still_returns_server_info(self):
        from aictl.mcp_server import handle_request, SERVER_NAME
        resp = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(resp["result"]["serverInfo"]["name"], SERVER_NAME)


class TestServerDiscover(unittest.TestCase):
    def test_discover_works_without_prior_initialize(self):
        from aictl.mcp_server import handle_request
        # No initialize call before this -- proves it's callable standalone,
        # matching the RC's stateless-core requirement.
        resp = handle_request({"jsonrpc": "2.0", "id": 1, "method": "server/discover"})
        self.assertIsNotNone(resp)
        self.assertIn("protocolVersion", resp["result"])

    def test_discover_lists_supported_versions(self):
        from aictl.mcp_server import handle_request, SUPPORTED_MCP_VERSIONS
        resp = handle_request({"jsonrpc": "2.0", "id": 1, "method": "server/discover"})
        self.assertEqual(resp["result"]["supportedVersions"], list(SUPPORTED_MCP_VERSIONS))
        self.assertIn("2026-07-28", resp["result"]["supportedVersions"])

    def test_discover_includes_server_info_and_capabilities(self):
        from aictl.mcp_server import handle_request, SERVER_NAME, SERVER_VERSION
        resp = handle_request({"jsonrpc": "2.0", "id": 1, "method": "server/discover"})
        result = resp["result"]
        self.assertEqual(result["serverInfo"]["name"], SERVER_NAME)
        self.assertEqual(result["serverInfo"]["version"], SERVER_VERSION)
        self.assertIn("capabilities", result)


class TestMetaTolerance(unittest.TestCase):
    def test_meta_in_initialize_params_is_ignored_not_rejected(self):
        from aictl.mcp_server import handle_request
        resp = handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05",
                      "_meta": {"clientId": "test-client", "traceId": "abc123"}},
        })
        self.assertNotIn("error", resp)
        self.assertEqual(resp["result"]["protocolVersion"], "2024-11-05")

    def test_meta_in_tools_call_params_is_ignored_not_rejected(self):
        from aictl.mcp_server import handle_request
        resp = handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "aictl_health", "arguments": {},
                      "_meta": {"progressToken": "xyz"}},
        })
        self.assertNotIn("error", resp)


class TestToolsListTtlAndCacheScope(unittest.TestCase):
    def test_ttl_ms_present_and_positive(self):
        from aictl.mcp_server import handle_request
        resp = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertIn("ttlMs", resp["result"])
        self.assertGreater(resp["result"]["ttlMs"], 0)

    def test_cache_scope_present(self):
        from aictl.mcp_server import handle_request
        resp = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(resp["result"]["cacheScope"], "server")

    def test_tools_still_present_and_unchanged_count(self):
        from aictl.mcp_server import handle_request, TOOLS
        resp = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(len(resp["result"]["tools"]), len(TOOLS))


class TestErrorCodesUnaffected(unittest.TestCase):
    """RC changes -32002 -> -32602 for missing-resource-style errors --
    confirm aictl never emitted -32002 in the first place (nothing to
    migrate) and still uses -32601/-32602 correctly."""

    def test_no_32002_error_code_anywhere_in_source(self):
        from pathlib import Path
        src = Path(__file__).parent.parent / "aictl" / "mcp_server.py"
        self.assertNotIn("-32002", src.read_text())

    def test_missing_tool_name_is_32602(self):
        from aictl.mcp_server import handle_request
        resp = handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"arguments": {}},
        })
        self.assertEqual(resp["error"]["code"], -32602)

    def test_unknown_method_is_32601(self):
        from aictl.mcp_server import handle_request
        resp = handle_request({"jsonrpc": "2.0", "id": 1, "method": "nonexistent/method"})
        self.assertEqual(resp["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()

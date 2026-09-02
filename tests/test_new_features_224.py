"""Pass 224: the documented MCP setup was broken; the server never was.

Running the last unexercised documented path. The server itself is fine —
probed over its real stdio protocol:

    initialize -> {'name': 'aictl', 'version': '1.7.0'}
    tools/list -> 19 tools

The instructions for using it were not:

  * **README carried two `## MCP Server` sections** that duplicated and
    contradicted each other, the second sitting *after* `## License`.
  * **The second config block was not valid JSON.** It opened with
    `// Claude Desktop config (...)` inside a ```json fence, and JSON has no
    comments. Of every fenced json block in every document in the repository,
    it was the only one that failed to parse.
  * **The same block omitted the `mcpServers` wrapper** — `{"aictl": {...}}`,
    which no MCP host will load. The other block and the `claude_desktop_config.json`
    the repository ships both had it.

The broken block was the more inviting one: it carried the tool table and the
"ask naturally" tour, so it was the likelier thing to copy.

Wiring README into `docsync`'s tracked documents while fixing this immediately
surfaced a fourth stale claim nobody had looked for — `CLI (66 Python + 29 Go)`
against a real 80 — plus a headline badge reading `1840 tests | 150 modules`
against 4,053 and 182. The guard paid for itself on its first run, which is the
argument for guards over inspection.

The tests below check the general property rather than this instance: every
fenced json block in every document must parse, and any block configuring MCP
must agree with the file the repository ships.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

from aictl.core.cli_surface import mcp_declared_tools

SHIPPED_CONFIG = Path("claude_desktop_config.json")


def _json_blocks(path: Path) -> list[str]:
    return re.findall(r"```json\n(.*?)```", path.read_text(errors="replace"), re.S)


def _docs() -> list[Path]:
    return [p for p in sorted(Path(".").rglob("*.md"))
            if not any(x in p.parts for x in (".git", "node_modules"))]


class TestEveryDocumentedJsonParses(unittest.TestCase):
    """The general form of the defect, and free to check."""

    def test_all_fenced_json_blocks_parse(self):
        broken = []
        for doc in _docs():
            for index, block in enumerate(_json_blocks(doc), 1):
                try:
                    json.loads(block)
                except json.JSONDecodeError as exc:
                    broken.append(f"{doc} block {index}: {exc}")
        self.assertEqual(broken, [],
                         "a ```json block a reader would copy does not parse")

    def test_the_scanner_actually_finds_blocks(self):
        # Guards the guard: a regex that matched nothing would pass the test
        # above forever while checking nothing.
        self.assertTrue(any(_json_blocks(doc) for doc in _docs()))


class TestMcpConfigAgreesWithTheShippedFile(unittest.TestCase):
    def setUp(self):
        self.shipped = json.loads(SHIPPED_CONFIG.read_text())

    def test_shipped_config_has_the_wrapper(self):
        self.assertIn("mcpServers", self.shipped)
        self.assertIn("aictl", self.shipped["mcpServers"])

    def test_every_documented_mcp_block_has_the_wrapper(self):
        # `{"aictl": {...}}` without `mcpServers` is what hosts silently
        # refuse to load, and it is what the README shipped.
        for doc in _docs():
            for index, block in enumerate(_json_blocks(doc), 1):
                payload = json.loads(block)
                if "aictl" in payload and "mcpServers" not in payload:
                    self.fail(f"{doc} block {index} omits the mcpServers wrapper")

    def test_documented_command_matches_the_shipped_one(self):
        expected = self.shipped["mcpServers"]["aictl"]
        found = 0
        for doc in _docs():
            for block in _json_blocks(doc):
                payload = json.loads(block)
                entry = payload.get("mcpServers", {}).get("aictl")
                if entry is None:
                    continue
                found += 1
                self.assertEqual(entry["command"], expected["command"], str(doc))
                self.assertEqual(entry["args"], expected["args"], str(doc))
        self.assertGreater(found, 0, "no documented MCP config found at all")

    def test_readme_documents_the_section_once(self):
        headings = re.findall(r"^## MCP.*$", Path("README.md").read_text(),
                              re.M)
        self.assertEqual(len(headings), 1, f"duplicated MCP sections: {headings}")


class TestTheDocumentedCommandReallyServes(unittest.TestCase):
    """The path a user follows, executed rather than read."""

    def _rpc(self, *requests: dict) -> list[dict]:
        payload = "".join(json.dumps(r) + "\n" for r in requests)
        result = subprocess.run(
            [sys.executable, "-m", "aictl.mcp_server"],
            input=payload, capture_output=True, text=True, timeout=120,
            cwd=str(Path(__file__).resolve().parent.parent))
        return [json.loads(line) for line in result.stdout.splitlines()
                if line.strip().startswith("{")]

    def test_tools_list_responds(self):
        replies = self._rpc({"jsonrpc": "2.0", "id": 1,
                             "method": "tools/list", "params": {}})
        self.assertTrue(replies, "the server produced no JSON-RPC reply")
        self.assertIn("result", replies[0])

    def test_it_advertises_every_declared_tool(self):
        # Compared against the derived set, not a hardcoded 19 — the number
        # this session has already watched rot in four other places.
        replies = self._rpc({"jsonrpc": "2.0", "id": 1,
                             "method": "tools/list", "params": {}})
        served = {t["name"] for t in replies[0]["result"]["tools"]}
        self.assertEqual(served, mcp_declared_tools())

    def test_initialize_reports_the_project_version(self):
        from aictl.__main__ import VERSION

        replies = self._rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2026-07-28",
                                        "capabilities": {},
                                        "clientInfo": {"name": "t",
                                                       "version": "1"}}})
        self.assertEqual(replies[0]["result"]["serverInfo"]["version"], VERSION)


class TestDocsyncCoversTheFilesThatMakeClaims(unittest.TestCase):
    """README and the Makefile assert the test count; now they are synced."""

    def test_readme_and_makefile_are_tracked(self):
        from aictl.core.docsync import _TRACKED_DOCS

        for name in ("README.md", "Makefile", "CLAUDE.md", "RELEASE.md"):
            self.assertIn(name, _TRACKED_DOCS)



if __name__ == "__main__":
    unittest.main()

"""Pass 60 regression tests: MCP method validation, error truncation, atomic continuity writes."""

import json
import pathlib
import tempfile
import unittest


class TestMcpMethodValidation(unittest.TestCase):
    """handle_request must treat non-string method as unknown/error, not crash."""

    def test_integer_method_does_not_crash(self):
        from aictl.mcp_server import handle_request
        # Should not raise — must return an error response or None
        result = handle_request({"method": 123, "id": 1, "params": {}})
        # Either None (notification path) or a dict
        self.assertTrue(result is None or isinstance(result, dict))

    def test_none_method_does_not_crash(self):
        from aictl.mcp_server import handle_request
        result = handle_request({"method": None, "id": 1, "params": {}})
        self.assertTrue(result is None or isinstance(result, dict))

    def test_list_method_does_not_crash(self):
        from aictl.mcp_server import handle_request
        result = handle_request({"method": ["tools/list"], "id": 1, "params": {}})
        self.assertTrue(result is None or isinstance(result, dict))

    def test_valid_method_still_works(self):
        from aictl.mcp_server import handle_request
        result = handle_request({"method": "tools/list", "id": 1, "params": {}})
        self.assertIsNotNone(result)
        self.assertIn("result", result)

    def test_source_validates_method_type(self):
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "mcp_server.py").read_text()
        self.assertIn("isinstance(method, str)", src,
                      "handle_request must validate that method is a string")


class TestMcpErrorTruncation(unittest.TestCase):
    """Tool error messages must be truncated to prevent leaking secrets/paths."""

    def test_source_truncates_error_message(self):
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "mcp_server.py").read_text()
        # The error handler must limit exception string length
        self.assertIn("str(e)[:200]", src,
                      "Tool error handler must truncate exception to 200 chars")

    def test_error_message_is_short(self):
        from aictl.mcp_server import handle_tool
        # Trigger an "unknown tool" error response
        result = handle_tool("nonexistent_tool_xyz", {})
        self.assertIn("isError", result)
        # Extract the text
        text = result["content"][0]["text"]
        self.assertLessEqual(len(text), 300,
                             "Error message must be reasonably short")


class TestContinuityAtomicWrite(unittest.TestCase):
    """_save_index and save_context must use atomic writes."""

    def test_save_index_atomic_pattern_in_source(self):
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "runtime" / "continuity.py").read_text()
        # Must use .tmp + .replace() pattern
        self.assertIn(".with_suffix(\".tmp\")", src,
                      "continuity.py must use temp file for atomic writes")
        self.assertIn(".replace(", src,
                      "continuity.py must use Path.replace() for atomic rename")

    def _make_engine(self):
        from aictl.runtime.continuity import ContextContinuityEngine
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        return ContextContinuityEngine(tmpdir)

    def test_save_index_writes_valid_json(self):
        engine = self._make_engine()
        engine._save_index([])
        content = engine._index_path.read_text()
        parsed = json.loads(content)
        self.assertEqual(parsed, [])

    def test_no_tmp_file_left_after_save(self):
        engine = self._make_engine()
        engine._save_index([])
        tmp_files = list(engine.dir.glob("*.tmp"))
        self.assertEqual(tmp_files, [],
                         "No .tmp files should remain after successful atomic save")

    def test_index_survives_after_save(self):
        from aictl.runtime.continuity import ContextSnapshot
        engine = self._make_engine()
        snap = ContextSnapshot(
            snapshot_id="test-snap-1",
            engine="vllm",
            model="meta-llama/Meta-Llama-3-8B",
        )
        engine._save_index([snap])
        loaded = engine._load_index()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].snapshot_id, "test-snap-1")


if __name__ == "__main__":
    unittest.main()

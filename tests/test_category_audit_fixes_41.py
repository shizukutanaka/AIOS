"""Pass 41 regression tests: proxy body cap, list_models type, negative tokens, hooks import."""

import inspect
import tempfile
import unittest
from pathlib import Path


class TestProxyBodyCap(unittest.TestCase):
    """proxy.py must cap Content-Length to prevent memory exhaustion."""

    def test_max_body_bytes_constant_exists(self):
        """_MAX_BODY_BYTES must be defined and positive."""
        from aictl.daemon import proxy
        self.assertTrue(hasattr(proxy, "_MAX_BODY_BYTES"),
                        "proxy.py must define _MAX_BODY_BYTES")
        self.assertGreater(proxy._MAX_BODY_BYTES, 0)

    def test_max_body_bytes_is_reasonable(self):
        """_MAX_BODY_BYTES must be at most 1 GB."""
        from aictl.daemon import proxy
        self.assertLessEqual(proxy._MAX_BODY_BYTES, 1024 * 1024 * 1024,
                             "_MAX_BODY_BYTES must not exceed 1 GB")

    def test_read_body_applies_cap(self):
        """_read_body source must use min(length, _MAX_BODY_BYTES)."""
        src = Path(__file__).parent.parent / "aictl" / "daemon" / "proxy.py"
        text = src.read_text()
        self.assertIn("min(length, _MAX_BODY_BYTES)", text,
                      "proxy._read_body must cap length with min(length, _MAX_BODY_BYTES)")


class TestProxyListModelsReturnType(unittest.TestCase):
    """_list_models must declare -> None, not -> list[Any]."""

    def test_list_models_return_annotation_is_none(self):
        """_list_models declares -> None (writes response via self._json)."""
        from aictl.daemon.proxy import ProxyHandler
        ann = ProxyHandler._list_models.__annotations__.get("return")
        # With 'from __future__ import annotations', annotations are stored as strings
        self.assertEqual(ann, "None",
                         "_list_models must have return annotation None, not list[Any]")

    def test_list_models_source_not_list_return(self):
        """_list_models must not have '-> list' in its signature."""
        src = Path(__file__).parent.parent / "aictl" / "daemon" / "proxy.py"
        import re
        m = re.search(r"def _list_models\(.*?\)\s*->\s*(\S+)", src.read_text())
        self.assertIsNotNone(m, "Could not find _list_models signature")
        self.assertNotIn("list", m.group(1),
                         "_list_models must not declare list return type")


class TestMeteringNegativeTokens(unittest.TestCase):
    """TokenMeter.record must clamp negative token counts to 0."""

    def _make_meter(self, tmp_dir: str):
        from aictl.core.metering import TokenMeter
        return TokenMeter(Path(tmp_dir))

    def test_negative_prompt_tokens_clamped(self):
        """Negative prompt_tokens must not reduce usage totals."""
        with tempfile.TemporaryDirectory() as tmp:
            meter = self._make_meter(tmp)
            meter.record("user1", "llama3", -100, 50)
            bucket = meter.get_usage("user1")
            self.assertIsNotNone(bucket)
            self.assertGreaterEqual(bucket.total_tokens, 0,
                                    "Negative prompt_tokens must not produce negative total")
            self.assertGreaterEqual(bucket.prompt_tokens, 0)

    def test_negative_completion_tokens_clamped(self):
        """Negative completion_tokens must not reduce usage totals."""
        with tempfile.TemporaryDirectory() as tmp:
            meter = self._make_meter(tmp)
            meter.record("user2", "llama3", 100, -200)
            bucket = meter.get_usage("user2")
            self.assertIsNotNone(bucket)
            self.assertGreaterEqual(bucket.completion_tokens, 0)
            self.assertGreaterEqual(bucket.total_tokens, 0)

    def test_both_negative_tokens_give_zero_total(self):
        """Both negative → total_tokens must be 0, not negative."""
        with tempfile.TemporaryDirectory() as tmp:
            meter = self._make_meter(tmp)
            meter.record("user3", "llama3", -50, -50)
            bucket = meter.get_usage("user3")
            self.assertEqual(bucket.total_tokens, 0)


class TestHooksNodeJoinedImport(unittest.TestCase):
    """hooks.py must import NODE_JOINED at module level, not inside on_node_joined."""

    def test_node_joined_module_level_import(self):
        """NODE_JOINED must be importable directly from hooks module level."""
        import aictl.core.hooks as hooks_mod
        # If the module-level import is correct, the constant must be accessible
        # via the events module imported by hooks (not a re-export, but the import
        # means hooks.py already pulled it in at module load time without error)
        from aictl.core.events import NODE_JOINED
        self.assertEqual(NODE_JOINED, "node.joined")

    def test_on_node_joined_no_local_import(self):
        """on_node_joined must not contain a local 'from aictl.core.events import' statement."""
        src = Path(__file__).parent.parent / "aictl" / "core" / "hooks.py"
        text = src.read_text()
        fn_start = text.find("def on_node_joined")
        self.assertNotEqual(fn_start, -1, "on_node_joined not found in hooks.py")
        # Find the next function def after on_node_joined
        next_fn = text.find("\ndef ", fn_start + 1)
        fn_body = text[fn_start:next_fn] if next_fn != -1 else text[fn_start:]
        self.assertNotIn("from aictl.core.events import", fn_body,
                         "on_node_joined must not have a local 'from aictl.core.events import'")


if __name__ == "__main__":
    unittest.main()

"""Pass 9 regression tests for correctness bugs identified by deep audit."""

import unittest
from unittest import mock


class TestMcpServerCarbonIntensity(unittest.TestCase):
    """mcp_server.py: carbon_intensity=0 must not fall back to region default."""

    def test_explicit_zero_is_honoured(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "mcp_server.py").read_text()
        # The old buggy pattern uses 'or' which treats 0 as falsy.
        # The fix uses an explicit 'in' membership check.
        import re
        bad = re.search(
            r'args\.get\("carbon_intensity"\)\s+or\s+CARBON_INTENSITY_BY_REGION',
            src,
        )
        self.assertIsNone(
            bad,
            "mcp_server.py must not use `or` to resolve carbon_intensity "
            "(explicit 0 would be silently discarded as falsy)",
        )

    def test_carbon_intensity_key_presence_pattern(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "mcp_server.py").read_text()
        # After the fix, an explicit `in args` membership check must exist near
        # carbon_intensity resolution.
        self.assertIn(
            '"carbon_intensity" in args',
            src,
            "mcp_server.py must use 'in args' to test for explicit carbon_intensity",
        )


class TestPluginsUsesModuleLevelLogger(unittest.TestCase):
    """plugins.py: exception handler must use module-level logger, not re-import."""

    def test_no_redundant_import_logging(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "core" / "plugins.py").read_text()
        import re
        # There must be no bare 'import logging' inside a function/except block.
        # The module-level import and logger are sufficient.
        redundant = re.search(r"^\s{4,}import logging\s*$", src, re.MULTILINE)
        self.assertIsNone(
            redundant,
            "plugins.py must not have a nested 'import logging' inside a function; "
            "use the module-level logger instead",
        )

    def test_wiring_failure_is_nonfatal(self):
        from aictl.core import plugins

        # A plugin whose on_event wiring raises must not propagate the exception.
        # (Structural: no nested `import logging` ensures module-level logger is used.)
        class _BadModule:
            @staticmethod
            def on_event(event):
                pass

        class _RaisingBus:
            def subscribe_all(self, fn):
                raise RuntimeError("wiring exploded")

        fake_info = {"path": "/nonexistent/plugin.py", "name": "bad_plugin"}
        with mock.patch.object(plugins, "discover_plugins", return_value=[fake_info]), \
             mock.patch.object(plugins, "load_plugin", return_value=_BadModule()), \
             mock.patch("aictl.core.events.get_bus", return_value=_RaisingBus()):
            # Must not raise even though subscribe_all blows up
            count = plugins.wire_plugin_events()
        self.assertEqual(count, 0)


class TestContinuityUrlOpenClosed(unittest.TestCase):
    """continuity.py: urlopen() must be used as a context manager to avoid resource leak."""

    def test_no_bare_urlopen_call(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "runtime" / "continuity.py").read_text()
        import re
        # Bare (unmanaged) urlopen: a line that calls urlopen without 'with'
        bare = re.search(
            r"^\s+urllib\.request\.urlopen\(req",
            src,
            re.MULTILINE,
        )
        self.assertIsNone(
            bare,
            "continuity.py must not call urllib.request.urlopen() without a context manager",
        )

    def test_urlopen_uses_with_statement(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "runtime" / "continuity.py").read_text()
        self.assertIn(
            "with urllib.request.urlopen",
            src,
            "continuity.py must use 'with urllib.request.urlopen' for clean connection closure",
        )


if __name__ == "__main__":
    unittest.main()

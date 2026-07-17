"""Pass 38 regression tests: info.py rest_endpoints stale (22→30)."""

import pathlib
import unittest


class TestInfoRestEndpoints(unittest.TestCase):
    """aictl/cmd/info.py must report 30 REST endpoints, not 22."""

    def test_info_py_not_22_rest(self):
        """info.py must not say rest_endpoints: 22 (stale)."""
        src = (
            pathlib.Path(__file__).parent.parent / "aictl" / "cmd" / "info.py"
        ).read_text()
        self.assertNotIn(
            '"rest_endpoints": 22',
            src,
            'aictl/cmd/info.py still has "rest_endpoints": 22 — update to 30.',
        )

    def test_info_py_has_30_rest(self):
        """info.py must say rest_endpoints: 30."""
        src = (
            pathlib.Path(__file__).parent.parent / "aictl" / "cmd" / "info.py"
        ).read_text()
        self.assertIn(
            '"rest_endpoints": 30',
            src,
            'aictl/cmd/info.py must have "rest_endpoints": 30.',
        )

    def test_info_cmd_rest_endpoints_runtime(self):
        """info command must return rest_endpoints=30 at runtime."""
        from aictl.cmd.info import run
        import argparse
        args = argparse.Namespace(json=True, func=None)
        import io
        import sys
        import json
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            run(args)
        except SystemExit:
            pass
        finally:
            sys.stdout = old_stdout
        output = buf.getvalue()
        if output.strip():
            data = json.loads(output)
            self.assertEqual(
                data.get("rest_endpoints"), 30,
                f"info --json rest_endpoints expected 30, got {data.get('rest_endpoints')}",
            )

    def test_aiosd_get_route_count(self):
        """aiosd.py GET route dict must have at least 22 entries."""
        import re
        src = (
            pathlib.Path(__file__).parent.parent
            / "aictl" / "daemon" / "aiosd.py"
        ).read_text()
        get_block_start = src.index("def do_GET")
        get_block_end = src.index("def do_POST")
        get_block = src[get_block_start:get_block_end]
        # Count entries like "/v1/...": or "/metrics":
        count = len(re.findall(r'"/(?:v1/[^"]+|metrics)":', get_block))
        self.assertGreaterEqual(
            count, 22,
            f"do_GET route dict has {count} entries, expected ≥22",
        )

    def test_aiosd_post_route_count(self):
        """aiosd.py POST route dict must have at least 8 entries."""
        import re
        src = (
            pathlib.Path(__file__).parent.parent
            / "aictl" / "daemon" / "aiosd.py"
        ).read_text()
        post_idx = src.index("def do_POST")
        # Grab first 600 chars of do_POST for the route dict
        post_section = src[post_idx:post_idx + 600]
        count = len(re.findall(r'"/v1/', post_section))
        self.assertGreaterEqual(
            count, 8,
            f"do_POST route dict has {count} entries, expected ≥8",
        )


if __name__ == "__main__":
    unittest.main()

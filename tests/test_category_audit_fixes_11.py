"""Pass 11 regression tests for correctness bugs identified by deep audit."""

import unittest


class TestWatchNeverCrashes(unittest.TestCase):
    """watch.py: transient render errors must not crash the monitor loop."""

    def test_watch_run_survives_render_error(self):
        import argparse
        from unittest import mock
        from aictl.cmd import watch

        call_count = [0]

        def bad_render(store, config):
            call_count[0] += 1
            if call_count[0] == 1:
                raise OSError("state file unreadable")
            # Second call succeeds — simulates transient error
            # raise KeyboardInterrupt to exit the loop cleanly
            raise KeyboardInterrupt

        args = argparse.Namespace(state_dir=None, interval=0)
        with mock.patch.object(watch, "_render", side_effect=bad_render):
            rc = watch.run(args)
        # Must not propagate the OSError; loop continues until KeyboardInterrupt
        self.assertEqual(rc, 0)
        self.assertEqual(call_count[0], 2, "loop must have retried after transient error")


class TestSpecParamBillions(unittest.TestCase):
    """spec.py: _pb() must not match short suffix inside longer one (e.g. '1b' in '14b')."""

    def _capture(self, model_name):
        import io, json, argparse
        from contextlib import redirect_stdout
        from aictl.cmd import spec
        a = argparse.Namespace(target=model_name, draft="llama3.2:1b", gamma=4, json=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            spec.run_bench(a)
        return json.loads(buf.getvalue())

    def test_14b_not_matched_as_1b(self):
        result_14b = self._capture("llama-14b")
        result_1b = self._capture("llama:1b")

        # With a 14B target the draft/target ratio is smaller → higher speedup.
        # Bug: "1b" matched inside "14b" → tp==1.0 for both → same speedup.
        self.assertGreater(
            result_14b["estimated_speedup"],
            result_1b["estimated_speedup"],
            "14B target must produce higher speedup than 1B target "
            "(bug: '1b' substring matched inside '14b', making both parse as 1B)",
        )

    def test_no_short_before_long(self):
        import pathlib, re
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "cmd" / "spec.py").read_text()
        # After fix, the list must be sorted by descending length before iteration
        self.assertIn(
            "sorted(",
            src,
            "spec.py _pb() must sort patterns by descending length to avoid '1b' matching inside '14b'",
        )


if __name__ == "__main__":
    unittest.main()

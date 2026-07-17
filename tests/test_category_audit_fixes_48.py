"""Pass 48 regression tests: Prometheus guard/cascade metrics and cascade stats persistence."""

import json
import pathlib
import tempfile
import unittest


def _call_emit_value_prop(extra_env: dict | None = None):
    """Helper: call _emit_value_prop_metrics and return lines as a string."""
    import os
    from unittest.mock import patch
    from aictl.metrics import prometheus as pmod
    lines: list = []
    if extra_env:
        with patch.dict(os.environ, extra_env):
            pmod._emit_value_prop_metrics(lines)
    else:
        pmod._emit_value_prop_metrics(lines)
    return "\n".join(lines)


class TestPrometheusGuardMetrics(unittest.TestCase):
    """prometheus.py must expose guard scan/block counters."""

    def test_guard_metrics_present_when_perf_has_guard(self):
        """If perf summary has guard data, emit includes aios_guard_scans_total."""
        from unittest.mock import patch

        fake_summary = {
            "guard": {"count": 42, "failures": 7,
                      "p50_ms": 1.0, "p95_ms": 2.0, "p99_ms": 3.0},
        }
        with patch("aictl.core.perf.summary", return_value=fake_summary):
            output = _call_emit_value_prop()
        self.assertIn("aios_guard_scans_total", output)
        self.assertIn("42", output)

    def test_guard_block_metric_present(self):
        """Guard blocks counter appears when perf has guard failures."""
        from unittest.mock import patch

        fake_summary = {
            "guard": {"count": 10, "failures": 3,
                      "p50_ms": 1.0, "p95_ms": 2.0, "p99_ms": 3.0},
        }
        with patch("aictl.core.perf.summary", return_value=fake_summary):
            output = _call_emit_value_prop()
        self.assertIn("aios_guard_blocks_total", output)
        self.assertIn("3", output)

    def test_guard_metrics_no_crash_when_no_perf_data(self):
        """Must not crash when guard has no perf history."""
        from unittest.mock import patch

        with patch("aictl.core.perf.summary", return_value={}):
            output = _call_emit_value_prop()
        self.assertIsInstance(output, str)


class TestPrometheusCascadeMetrics(unittest.TestCase):
    """prometheus.py must expose cascade run/escalation counters."""

    def test_cascade_metrics_present_when_stats_file_exists(self):
        """_emit_value_prop_metrics reads cascade_stats.json and emits the two counters."""
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            stats = {"total_runs": 100, "escalations": 15}
            stats_path = pathlib.Path(tmpdir) / "cascade_stats.json"
            stats_path.write_text(json.dumps(stats))

            with patch("aictl.core.perf.summary", return_value={}):
                output = _call_emit_value_prop({"AIOS_STATE_DIR": tmpdir})

        self.assertIn("aios_route_cascade_runs_total", output)
        self.assertIn("100", output)
        self.assertIn("aios_route_cascade_escalations_total", output)
        self.assertIn("15", output)

    def test_cascade_metrics_no_crash_when_no_stats_file(self):
        """Must not crash when cascade_stats.json doesn't exist."""
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("aictl.core.perf.summary", return_value={}):
                output = _call_emit_value_prop({"AIOS_STATE_DIR": tmpdir})
        self.assertIsInstance(output, str)


class TestCascadeStatsPersistence(unittest.TestCase):
    """_record_cascade_stat() must increment counters in cascade_stats.json."""

    def test_first_run_no_escalation(self):
        """First call creates the file with total_runs=1, escalations=0."""
        import os
        from unittest.mock import patch
        from aictl.cmd.route import _record_cascade_stat

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"AIOS_STATE_DIR": tmpdir}):
                _record_cascade_stat(False)
            data = json.loads((pathlib.Path(tmpdir) / "cascade_stats.json").read_text())
        self.assertEqual(data["total_runs"], 1)
        self.assertEqual(data["escalations"], 0)

    def test_escalated_run_increments_both(self):
        """An escalated run increments both total_runs and escalations."""
        import os
        from unittest.mock import patch
        from aictl.cmd.route import _record_cascade_stat

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"AIOS_STATE_DIR": tmpdir}):
                _record_cascade_stat(True)
            data = json.loads((pathlib.Path(tmpdir) / "cascade_stats.json").read_text())
        self.assertEqual(data["total_runs"], 1)
        self.assertEqual(data["escalations"], 1)

    def test_accumulation_across_calls(self):
        """Multiple calls accumulate correctly."""
        import os
        from unittest.mock import patch
        from aictl.cmd.route import _record_cascade_stat

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"AIOS_STATE_DIR": tmpdir}):
                _record_cascade_stat(False)
                _record_cascade_stat(True)
                _record_cascade_stat(False)
                _record_cascade_stat(True)
            data = json.loads((pathlib.Path(tmpdir) / "cascade_stats.json").read_text())
        self.assertEqual(data["total_runs"], 4)
        self.assertEqual(data["escalations"], 2)

    def test_stat_persistence_tolerates_corrupt_file(self):
        """If the stats file is corrupt JSON, record_cascade_stat silently resets it."""
        import os
        from unittest.mock import patch
        from aictl.cmd.route import _record_cascade_stat

        with tempfile.TemporaryDirectory() as tmpdir:
            corrupt = pathlib.Path(tmpdir) / "cascade_stats.json"
            corrupt.write_text("{not valid json")
            with patch.dict(os.environ, {"AIOS_STATE_DIR": tmpdir}):
                _record_cascade_stat(True)
            data = json.loads(corrupt.read_text())
        self.assertEqual(data["total_runs"], 1)
        self.assertEqual(data["escalations"], 1)


if __name__ == "__main__":
    unittest.main()

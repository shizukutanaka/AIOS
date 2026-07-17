"""Coverage tests for cmd modules (batch 3).

Exercises security, log, recipe, warmup, scale, otel, image, doctor, report
command handlers against isolated temporary state directories.
"""

import argparse
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _ns(**kw):
    return argparse.Namespace(**kw)


def _capture(fn, args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fn(args)
    out = buf.getvalue().strip()
    return rc, out


def _capture_json(fn, args):
    rc, out = _capture(fn, args)
    return rc, (json.loads(out) if out else None)


# ─── security ─────────────────────────────────────────────

class TestSecurityCmd(unittest.TestCase):
    def test_json_returns_score_and_findings(self):
        from aictl.cmd import security
        rc, parsed = _capture_json(security.run, _ns(state_dir=None, json=True))
        self.assertEqual(rc, 0)
        self.assertIn("score", parsed)
        self.assertIn("checks_passed", parsed)
        self.assertIn("checks_total", parsed)
        self.assertIn("findings", parsed)
        self.assertIsInstance(parsed["score"], (int, float))

    def test_score_in_valid_range(self):
        from aictl.cmd import security
        rc, parsed = _capture_json(security.run, _ns(state_dir=None, json=True))
        self.assertEqual(rc, 0)
        self.assertGreaterEqual(parsed["score"], 0)
        self.assertLessEqual(parsed["score"], 100)

    def test_with_state_dir(self):
        from aictl.cmd import security
        with tempfile.TemporaryDirectory() as d:
            rc, parsed = _capture_json(security.run, _ns(state_dir=d, json=True))
        self.assertEqual(rc, 0)
        self.assertIn("score", parsed)


# ─── log ──────────────────────────────────────────────────

class TestLogCmd(unittest.TestCase):
    def test_json_empty_returns_list(self):
        from aictl.cmd import log
        rc, parsed = _capture_json(log.run, _ns(n=5, level="", rotate=False, json=True))
        self.assertEqual(rc, 0)
        self.assertIsInstance(parsed, list)

    def test_rotate_returns_0(self):
        from aictl.cmd import log
        rc, out = _capture(log.run, _ns(n=20, level="", rotate=True, json=False))
        self.assertEqual(rc, 0)
        self.assertIn("Rotated", out)

    def test_level_filter_accepted(self):
        from aictl.cmd import log
        rc, parsed = _capture_json(log.run, _ns(n=5, level="error", rotate=False, json=True))
        self.assertEqual(rc, 0)
        self.assertIsInstance(parsed, list)


# ─── recipe ───────────────────────────────────────────────

class TestRecipeCmd(unittest.TestCase):
    def test_list_json_returns_names(self):
        from aictl.cmd import recipe
        rc, parsed = _capture_json(recipe.run_list, _ns(json=True))
        self.assertEqual(rc, 0)
        self.assertIsInstance(parsed, list)
        self.assertGreater(len(parsed), 0)
        # All entries are strings (recipe names)
        for name in parsed:
            self.assertIsInstance(name, str)

    def test_list_contains_known_recipes(self):
        from aictl.cmd import recipe
        rc, parsed = _capture_json(recipe.run_list, _ns(json=True))
        self.assertEqual(rc, 0)
        self.assertIn("local-chat", parsed)

    def test_run_unknown_recipe_returns_1(self):
        from aictl.cmd import recipe
        with tempfile.TemporaryDirectory() as d:
            rc, _ = _capture(
                recipe.run_recipe,
                _ns(state_dir=Path(d), name="no-such-recipe-xyz", dry_run=False, json=False),
            )
        self.assertEqual(rc, 1)

    def test_run_dry_run_known_recipe(self):
        from aictl.cmd import recipe
        from aictl.stack.manifest import list_recipes
        names = list_recipes()
        if not names:
            self.skipTest("no recipes available")
        with tempfile.TemporaryDirectory() as d:
            rc, parsed = _capture_json(
                recipe.run_recipe,
                _ns(state_dir=Path(d), name=names[0], dry_run=True, json=True),
            )
        self.assertEqual(rc, 0)
        self.assertIn("recipe", parsed)
        self.assertIn("services", parsed)


# ─── warmup ───────────────────────────────────────────────

class TestWarmupCmd(unittest.TestCase):
    def test_stats_empty_state_json(self):
        from aictl.cmd import warmup
        with tempfile.TemporaryDirectory() as d:
            rc, parsed = _capture_json(
                warmup.run_stats, _ns(state_dir=Path(d), json=True))
        self.assertEqual(rc, 0)
        self.assertIsInstance(parsed, list)

    def test_run_empty_state_no_crash(self):
        from aictl.cmd import warmup
        with tempfile.TemporaryDirectory() as d:
            rc, out = _capture(
                warmup.run_warmup, _ns(state_dir=Path(d), top=3, json=False))
        self.assertEqual(rc, 0)
        # With no history the handler prints a human-readable message
        self.assertIn("No model usage history", out)


# ─── scale ────────────────────────────────────────────────

class TestScaleCmd(unittest.TestCase):
    def test_keda_json_manifest_structure(self):
        from aictl.cmd import scale
        rc, parsed = _capture_json(
            scale.run_keda,
            _ns(deployment="my-llm", engine="vllm",
                min=1, max=8, threshold=5,
                prometheus="http://prometheus:9090"),
        )
        self.assertEqual(rc, 0)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["kind"], "ScaledObject")
        self.assertIn("spec", parsed)

    def test_keda_deployment_name_in_manifest(self):
        from aictl.cmd import scale
        rc, parsed = _capture_json(
            scale.run_keda,
            _ns(deployment="inference-svc", engine="sglang",
                min=2, max=16, threshold=10,
                prometheus="http://prom:9090"),
        )
        self.assertEqual(rc, 0)
        # Manifest name references the deployment
        self.assertIn("inference-svc", parsed["metadata"]["name"])

    def test_hpa_manifest_structure(self):
        from aictl.cmd import scale
        rc, parsed = _capture_json(
            scale.run_hpa,
            _ns(deployment="my-llm", min=1, max=4),
        )
        self.assertEqual(rc, 0)
        self.assertEqual(parsed["kind"], "HorizontalPodAutoscaler")
        self.assertIn("spec", parsed)
        spec = parsed["spec"]
        self.assertEqual(spec["minReplicas"], 1)
        self.assertEqual(spec["maxReplicas"], 4)


# ─── otel ─────────────────────────────────────────────────

class TestOtelCmd(unittest.TestCase):
    def test_config_stdout_is_yaml(self):
        from aictl.cmd import otel
        rc, out = _capture(otel.run_config, _ns(state_dir=None, output=""))
        self.assertEqual(rc, 0)
        self.assertIn("receivers", out)
        self.assertIn("exporters", out)

    def test_config_writes_to_file(self):
        from aictl.cmd import otel
        with tempfile.TemporaryDirectory() as d:
            out_path = Path(d) / "otel.yaml"
            rc, _ = _capture(
                otel.run_config, _ns(state_dir=None, output=str(out_path)))
            self.assertEqual(rc, 0)
            self.assertTrue(out_path.exists())
            content = out_path.read_text()
            self.assertIn("receivers", content)


# ─── image ────────────────────────────────────────────────

class TestImageCmd(unittest.TestCase):
    def test_formats_json_returns_list(self):
        from aictl.cmd import image
        rc, parsed = _capture_json(image.run_formats, _ns(json=True))
        self.assertEqual(rc, 0)
        self.assertIsInstance(parsed, list)
        self.assertGreater(len(parsed), 0)

    def test_formats_entries_have_required_keys(self):
        from aictl.cmd import image
        rc, parsed = _capture_json(image.run_formats, _ns(json=True))
        self.assertEqual(rc, 0)
        for entry in parsed:
            self.assertIn("format", entry)
            self.assertIn("description", entry)

    def test_formats_includes_qcow2(self):
        from aictl.cmd import image
        rc, parsed = _capture_json(image.run_formats, _ns(json=True))
        self.assertEqual(rc, 0)
        formats = [e["format"] for e in parsed]
        self.assertIn("qcow2", formats)


# ─── doctor ───────────────────────────────────────────────

class TestDoctorCmd(unittest.TestCase):
    def test_json_has_hardware_key(self):
        from aictl.cmd import doctor
        with tempfile.TemporaryDirectory() as d:
            rc, parsed = _capture_json(
                doctor.run, _ns(state_dir=Path(d), deep=False, json=True))
        self.assertEqual(rc, 0)
        self.assertIn("hardware", parsed)

    def test_json_deep_adds_security_and_fabric(self):
        from aictl.cmd import doctor
        with tempfile.TemporaryDirectory() as d:
            rc, parsed = _capture_json(
                doctor.run, _ns(state_dir=Path(d), deep=True, json=True))
        self.assertEqual(rc, 0)
        self.assertIn("hardware", parsed)
        self.assertIn("security", parsed)
        self.assertIn("fabric", parsed)


# ─── report ───────────────────────────────────────────────

class TestReportCmd(unittest.TestCase):
    def test_json_has_all_top_level_sections(self):
        from aictl.cmd import report
        with tempfile.TemporaryDirectory() as d:
            rc, parsed = _capture_json(
                report.run, _ns(state_dir=Path(d), output="", json=True))
        self.assertEqual(rc, 0)
        for section in ("version", "hardware", "security", "fabric", "recipes", "costs"):
            self.assertIn(section, parsed, f"missing section: {section}")

    def test_json_version_matches_constants(self):
        from aictl.cmd import report
        from aictl.core.constants import AICTL_VERSION
        with tempfile.TemporaryDirectory() as d:
            rc, parsed = _capture_json(
                report.run, _ns(state_dir=Path(d), output="", json=True))
        self.assertEqual(rc, 0)
        self.assertEqual(parsed["version"], AICTL_VERSION)

    def test_writes_markdown_to_file(self):
        from aictl.cmd import report
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "report.md"
            rc, _ = _capture(
                report.run,
                _ns(state_dir=Path(d), output=str(out), json=False),
            )
            self.assertEqual(rc, 0)
            self.assertTrue(out.exists())
            md = out.read_text()
        self.assertIn("#", md)  # Markdown headings present


if __name__ == "__main__":
    unittest.main()

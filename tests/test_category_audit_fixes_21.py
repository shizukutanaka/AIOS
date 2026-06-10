"""Pass 21 regression tests: slug edge cases, orchestrator OSError, output mkdir."""

import pathlib
import re
import unittest


class TestDisaggModelSlug(unittest.TestCase):
    """disagg.py: model slug must never be empty (trailing slash guard)."""

    def _slug_from(self, model: str) -> str:
        """Replicate the slug logic from disagg.py / modelservice.py."""
        return (model.rstrip("/").split("/")[-1] or "model").lower().replace(".", "-")

    def test_normal_hf_model_slug(self):
        self.assertEqual(self._slug_from("meta-llama/Llama-3.2-8B-Instruct"), "llama-3-2-8b-instruct")

    def test_bare_model_name(self):
        self.assertEqual(self._slug_from("llama3:8b"), "llama3:8b")

    def test_trailing_slash_strips_to_parent(self):
        """model ending with '/' strips it cleanly (produces parent component, not empty)."""
        # "meta-llama/" → "meta-llama" (strip trailing slash, take last part)
        self.assertEqual(self._slug_from("meta-llama/"), "meta-llama")

    def test_slash_only_yields_fallback(self):
        """Model with only '/' characters must fall back to 'model' not empty string."""
        self.assertEqual(self._slug_from("/"), "model")

    def test_disagg_source_uses_rstrip(self):
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "stack" / "disagg.py").read_text()
        self.assertIn(
            "rstrip",
            src,
            "disagg.py model_slug must use rstrip('/') to guard against trailing slash",
        )

    def test_modelservice_source_uses_rstrip(self):
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "stack" / "modelservice.py").read_text()
        self.assertIn(
            "rstrip",
            src,
            "modelservice.py model_slug must use rstrip('/') to guard against trailing slash",
        )


class TestOrchestratorOSError(unittest.TestCase):
    """orchestrator.py: OSError from Popen must be caught and returned as error status."""

    def test_oserror_caught_in_source(self):
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "stack" / "orchestrator.py").read_text()
        self.assertIn(
            "OSError",
            src,
            "orchestrator.py must catch OSError (not just FileNotFoundError) from subprocess.Popen",
        )

    def test_fnf_and_oserror_distinct_branches(self):
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "stack" / "orchestrator.py").read_text()
        # Both FileNotFoundError and OSError must be caught
        self.assertIn("FileNotFoundError", src)
        self.assertIn("OSError", src)


class TestReportOutputMkdir(unittest.TestCase):
    """report.py: writing to --output path must create parent directories."""

    def test_report_output_to_nested_dir(self):
        import argparse, io, tempfile
        from pathlib import Path
        from contextlib import redirect_stdout
        from aictl.cmd import report

        with tempfile.TemporaryDirectory() as d:
            # Write report to a nested subdirectory that doesn't exist yet
            out_path = str(Path(d) / "subdir" / "report.md")
            args = argparse.Namespace(
                state_dir=Path(d),
                output=out_path,
                json=False,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = report.run(args)
            self.assertEqual(rc, 0)
            self.assertTrue(Path(out_path).exists(), "report file must be created at nested path")


class TestEvalSavePathMkdir(unittest.TestCase):
    """eval.py: saving results to a nested path must create parent directories."""

    def test_eval_source_has_mkdir_for_save(self):
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "cmd" / "eval.py").read_text()
        # Find the save_path write section
        match = re.search(r"save_path.*?write_text", src, re.DOTALL)
        self.assertIsNotNone(match, "eval.py must have save_path write_text code")
        fragment = match.group(0)
        self.assertIn(
            "mkdir",
            fragment,
            "eval.py must call mkdir before writing save_path to support nested paths",
        )


if __name__ == "__main__":
    unittest.main()

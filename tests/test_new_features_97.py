"""Pass 97 (loop): deterministic filesystem iteration.

New lens: output determinism. Several commands iterated rglob results in
OS-dependent order. Worst case: batch's `files[:10]` slice picked a
*non-deterministic subset* of files to process. Sorting makes both the selected
subset and the output order reproducible.
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


class TestFormatsScanSorted(unittest.TestCase):

    def test_detect_model_dir_returns_sorted_paths(self):
        from aictl.runtime.formats import detect_model_dir
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name in ["m.gguf", "a.gguf", "z.safetensors", "b.gguf", "c.onnx"]:
                (root / name).write_bytes(b"\x00")
            paths = [r.metadata["path"] for r in detect_model_dir(root)]
            self.assertEqual(paths, sorted(paths))


class TestBatchDeterministicSelection(unittest.TestCase):

    def test_summarize_processes_sorted_first_ten(self):
        import aictl
        from aictl.cmd.batch import _task_summarize
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            # 15 .md files created in scrambled order; only 10 are processed.
            for name in ["m", "a", "f", "b", "c", "d", "e", "g", "h", "i",
                         "j", "k", "l", "n", "o"]:
                (root / f"{name}.md").write_text("text")
            buf = io.StringIO()
            with patch.object(aictl.ai, "ask", return_value="summary"), \
                 redirect_stdout(buf):
                _task_summarize(str(root), "mock")
            processed = [ln.strip().split(".md")[0] for ln in buf.getvalue().splitlines()
                         if ".md:" in ln]
            # Must be exactly the first 10 names alphabetically, in order.
            expected = sorted("m a f b c d e g h i j k l n o".split())[:10]
            self.assertEqual(processed, expected)

    def test_selection_is_stable_across_runs(self):
        import aictl
        from aictl.cmd.batch import _task_summarize
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for i in range(20):
                (root / f"f{i:02d}.md").write_text("x")

            def run_once():
                buf = io.StringIO()
                with patch.object(aictl.ai, "ask", return_value="s"), redirect_stdout(buf):
                    _task_summarize(str(root), "mock")
                return [ln for ln in buf.getvalue().splitlines() if ".md:" in ln]

            self.assertEqual(run_once(), run_once())


if __name__ == "__main__":
    unittest.main()

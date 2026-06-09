"""Pass 12 regression tests for correctness bugs identified by deep audit."""

import unittest
from unittest import mock
import os, time


class TestModelCacheCleanActuallyDeletes(unittest.TestCase):
    """model.py: --clean must call clean_stale(dry_run=False), not dry_run=True."""

    def test_clean_flag_passes_dry_run_false(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "cmd" / "model.py").read_text()
        import re
        # Must NOT call clean_stale with dry_run=True
        bad = re.search(r"clean_stale\([^)]*dry_run\s*=\s*True", src)
        self.assertIsNone(
            bad,
            "model.py must not call clean_stale(dry_run=True) when --clean is set; "
            "dry_run=True means nothing gets deleted, making --clean a no-op",
        )

    def test_clean_stale_dry_run_false_removes_files(self):
        import tempfile, pathlib
        from aictl.runtime.cache import CacheReport, CacheEntry, clean_stale

        with tempfile.TemporaryDirectory() as d:
            old_file = pathlib.Path(d) / "old_model.gguf"
            old_file.write_bytes(b"x" * 1024)
            # Make file appear old by modifying mtime (32 days ago)
            old_mtime = time.time() - 32 * 86400
            os.utime(str(old_file), (old_mtime, old_mtime))

            entry = CacheEntry(
                name="old_model.gguf",
                path=str(old_file),
                size_bytes=1024,
                last_accessed=old_mtime,
                source="ollama",
            )
            report = CacheReport(
                entries=[entry],
                total_bytes=1024,
                locations={"ollama": 1024},
            )

            removed = clean_stale(report, days=30, dry_run=False)
            self.assertEqual(len(removed), 1, "one stale entry should have been removed")
            self.assertFalse(old_file.exists(), "stale file must be physically deleted")

    def test_clean_stale_dry_run_true_preserves_files(self):
        import tempfile, pathlib
        from aictl.runtime.cache import CacheReport, CacheEntry, clean_stale

        with tempfile.TemporaryDirectory() as d:
            old_file = pathlib.Path(d) / "kept.gguf"
            old_file.write_bytes(b"y" * 512)
            old_mtime = time.time() - 32 * 86400
            os.utime(str(old_file), (old_mtime, old_mtime))

            entry = CacheEntry(
                name="kept.gguf",
                path=str(old_file),
                size_bytes=512,
                last_accessed=old_mtime,
                source="ollama",
            )
            report = CacheReport(entries=[entry], total_bytes=512, locations={"ollama": 512})

            removed = clean_stale(report, days=30, dry_run=True)
            self.assertEqual(len(removed), 1, "dry-run should still report the stale entry")
            self.assertTrue(old_file.exists(), "dry-run must not delete the file")


class TestSetupMultiGpuLabel(unittest.TestCase):
    """setup.py: multi-GPU systems must show GPU count in the label, not just first GPU name."""

    def test_multi_gpu_label_in_source(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "cmd" / "setup.py").read_text()
        self.assertIn(
            "len(hw.gpus) > 1",
            src,
            "setup.py must handle multi-GPU systems by showing GPU count, not just gpu[0].name",
        )


if __name__ == "__main__":
    unittest.main()

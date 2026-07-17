"""Pass 106 (loop): rag indexing caps per-file size (OOM guard).

New lens: resource bounds on input data. read_file used path.read_text(), pulling
the whole file into memory, so `rag index <dir>` containing a multi-GB log/dump
would OOM. read_file now skips any file over MAX_INDEX_FILE_BYTES (10 MB) before
reading — far larger than any real document.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class TestRagFileSizeCap(unittest.TestCase):

    def test_small_file_is_read(self):
        from aictl.core.rag import read_file
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "doc.txt"
            f.write_text("the quick brown fox")
            self.assertEqual(read_file(f), "the quick brown fox")

    def test_oversized_file_skipped_without_reading(self):
        from aictl.core.rag import read_file, MAX_INDEX_FILE_BYTES
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "huge.txt"
            # Sparse file just over the cap — never actually read into memory.
            with open(f, "wb") as fh:
                fh.seek(MAX_INDEX_FILE_BYTES + 1)
                fh.write(b"x")
            self.assertGreater(f.stat().st_size, MAX_INDEX_FILE_BYTES)
            self.assertIsNone(read_file(f))

    def test_file_at_cap_still_read(self):
        from aictl.core.rag import read_file, MAX_INDEX_FILE_BYTES
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "atcap.txt"
            f.write_bytes(b"a" * MAX_INDEX_FILE_BYTES)  # exactly the cap → allowed
            self.assertIsNotNone(read_file(f))


if __name__ == "__main__":
    unittest.main()

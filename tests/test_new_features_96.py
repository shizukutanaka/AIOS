"""Pass 96 (loop, Socratic new perspective): resource-leak guard.

New lens: resource lifecycle, not just logical correctness. A bare
``urllib.request.urlopen(...)`` leaks the response's socket/FD until GC. One such
leak existed (setup.py's vLLM health probe). The codebase convention is to wrap
every urlopen in ``with``; this test enforces that convention so the leak class
can't reappear.
"""

from __future__ import annotations

import pathlib
import re
import unittest


class TestNoUnclosedUrlopen(unittest.TestCase):

    def test_every_urlopen_is_context_managed(self):
        root = pathlib.Path(__file__).resolve().parent.parent / "aictl"
        offenders: list[str] = []
        for f in root.rglob("*.py"):
            lines = f.read_text().splitlines()
            for i, line in enumerate(lines, 1):
                if "urlopen(" not in line:
                    continue
                stripped = line.strip()
                # A definition/comment reference, not a call site.
                if stripped.startswith("#"):
                    continue
                # Acceptable: the call is the subject of a `with` on the same line,
                # or it continues a `with` started on the previous line.
                prev = lines[i - 2].strip() if i >= 2 else ""
                if "with " in line or prev.startswith("with ") or prev.endswith("("):
                    continue
                offenders.append(f"{f.relative_to(root)}:{i}: {stripped}")
        self.assertEqual(
            offenders, [],
            "urlopen calls must be wrapped in `with` (else the socket/FD leaks):\n"
            + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()

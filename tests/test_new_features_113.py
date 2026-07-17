"""Pass 113 (loop): reject sub-1 --context / --concurrent in `aictl fit`.

Context length and concurrency are physical quantities. `aictl fit` fed them
straight into the KV-cache estimate (kv ~ per_1k * context/1000 * concurrent),
so a negative or zero value produced a NEGATIVE kv_cache_mb that silently
under-reported total VRAM — making a model falsely appear to fit. The command
now validates both are >= 1 and exits non-zero with a clear message, matching
the existing empty-model-name guard.
"""

from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch, MagicMock


def _args(**kw):
    base = dict(model="llama3.1:70b", gpu="H100", context=8192,
               concurrent=1, use_case="", json=False)
    base.update(kw)
    return argparse.Namespace(**base)


class TestFitNumericValidation(unittest.TestCase):
    def test_negative_concurrent_rejected(self):
        from aictl.cmd.fit import run
        self.assertEqual(run(_args(concurrent=-5)), 1)

    def test_zero_concurrent_rejected(self):
        from aictl.cmd.fit import run
        self.assertEqual(run(_args(concurrent=0)), 1)

    def test_zero_context_rejected(self):
        from aictl.cmd.fit import run
        self.assertEqual(run(_args(context=0)), 1)

    def test_negative_context_rejected(self):
        from aictl.cmd.fit import run
        self.assertEqual(run(_args(context=-100000)), 1)

    def test_valid_inputs_never_yield_negative_kv(self):
        from aictl.cmd.fit import run
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run(_args(concurrent=4, context=8192, json=True))
        self.assertIn(rc, (0, 2))  # 0/2 = fits / doesn't fit, both valid
        d = json.loads(buf.getvalue())
        for name, q in d["quants"].items():
            self.assertGreaterEqual(q["kv_cache_mb"], 0, name)
            self.assertGreaterEqual(q["total_mb"], q["weights_mb"], name)


if __name__ == "__main__":
    unittest.main()

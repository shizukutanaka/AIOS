"""Pass 117 (loop): reject invalid replica bounds in `aictl scale keda/hpa`.

`ScalePolicy` wrote min/max replicas straight into KEDA ScaledObject and K8s
HPA manifests with no validation, so the CLI happily emitted manifests the
API server rejects on `kubectl apply`:

  - `scale keda mysvc --min 10 --max 2` → minReplicaCount=10, maxReplicaCount=2
    (min > max — invalid)
  - `scale keda mysvc --min -5`         → minReplicaCount=-5 (negative — invalid)

`ScalePolicy.__post_init__` now validates the bounds at construction (min >= 0,
max >= 1, min <= max), raising ValueError — surfaced by the top-level handler as
an "Invalid input" message with a non-zero exit. KEDA scale-to-zero (min == 0)
stays valid. The fix is in the shared policy object, so it covers both `keda`
and `hpa` (and any future caller) in one place.
"""

from __future__ import annotations

import argparse
import io
import unittest
from contextlib import redirect_stdout


class TestScalePolicyValidation(unittest.TestCase):
    def test_min_greater_than_max_rejected(self):
        from aictl.runtime.autoscaler import ScalePolicy
        with self.assertRaises(ValueError):
            ScalePolicy(min_replicas=10, max_replicas=2)

    def test_negative_min_rejected(self):
        from aictl.runtime.autoscaler import ScalePolicy
        with self.assertRaises(ValueError):
            ScalePolicy(min_replicas=-5, max_replicas=8)

    def test_zero_max_rejected(self):
        from aictl.runtime.autoscaler import ScalePolicy
        with self.assertRaises(ValueError):
            ScalePolicy(min_replicas=0, max_replicas=0)

    def test_scale_to_zero_allowed(self):
        from aictl.runtime.autoscaler import ScalePolicy
        p = ScalePolicy(min_replicas=0, max_replicas=8)  # KEDA scale-to-zero
        self.assertEqual(p.min_replicas, 0)

    def test_default_policy_valid(self):
        from aictl.runtime.autoscaler import ScalePolicy
        p = ScalePolicy()
        self.assertLessEqual(p.min_replicas, p.max_replicas)


class TestScaleCmdExitCodes(unittest.TestCase):
    def _keda(self, mn, mx):
        return argparse.Namespace(deployment="svc", engine="vllm", min=mn,
                                  max=mx, threshold=5,
                                  prometheus="http://prometheus:9090", json=False)

    def _hpa(self, mn, mx):
        return argparse.Namespace(deployment="svc", min=mn, max=mx, json=False)

    def test_keda_invalid_bounds_raise(self):
        from aictl.cmd.scale import run_keda
        # The handler builds ScalePolicy first; bad bounds raise before output.
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(ValueError):
                run_keda(self._keda(10, 2))
        self.assertEqual(buf.getvalue(), "")  # no partial manifest emitted

    def test_hpa_invalid_bounds_raise(self):
        from aictl.cmd.scale import run_hpa
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(ValueError):
                run_hpa(self._hpa(10, 2))
        self.assertEqual(buf.getvalue(), "")

    def test_keda_valid_bounds_emit_manifest(self):
        from aictl.cmd.scale import run_keda
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_keda(self._keda(1, 8))
        self.assertEqual(rc, 0)
        self.assertIn("ScaledObject", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

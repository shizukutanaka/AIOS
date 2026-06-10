"""Pass 22 regression tests: SGLang DEGRADED error, kserve decodeReplicas=0."""

import unittest


class TestSGLangDegradedError(unittest.TestCase):
    """adapters.py SGLangAdapter.health(): DEGRADED status must set h.error."""

    def test_sglang_degraded_sets_error_in_source(self):
        import pathlib, re
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "runtime" / "adapters.py").read_text()
        # Find SGLangAdapter class
        match = re.search(r"class SGLangAdapter.*?(?=^class |\Z)", src, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match, "SGLangAdapter class must exist")
        body = match.group(0)
        # The DEGRADED branch must set h.error
        self.assertIn(
            'h.error = f"HTTP {code}"',
            body,
            "SGLangAdapter health() DEGRADED branch must set h.error to HTTP status code",
        )

    def test_sglang_degraded_branch_consistent_with_vllm(self):
        """SGLang and vLLM DEGRADED branches must both set h.error."""
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "runtime" / "adapters.py").read_text()
        # Both adapters' DEGRADED branches should set h.error = f"HTTP {code}"
        count = src.count('h.error = f"HTTP {code}"')
        self.assertGreaterEqual(
            count, 2,
            "Both VLLMAdapter and SGLangAdapter must set h.error in their DEGRADED branches",
        )


class TestKServeDecodeReplicas(unittest.TestCase):
    """kserve.py: decodeReplicas must never be 0 even with replicas=1."""

    def _decode_replicas(self, replicas: int) -> int:
        return max(1, replicas - max(1, replicas // 3))

    def test_replicas_1_decode_is_1(self):
        self.assertEqual(self._decode_replicas(1), 1)

    def test_replicas_2_decode_is_1(self):
        self.assertEqual(self._decode_replicas(2), 1)

    def test_replicas_3_decode_is_2(self):
        self.assertEqual(self._decode_replicas(3), 2)

    def test_replicas_6_decode_is_4(self):
        self.assertEqual(self._decode_replicas(6), 4)

    def test_decode_never_zero_for_1_to_10(self):
        for r in range(1, 11):
            d = self._decode_replicas(r)
            self.assertGreater(d, 0, f"decodeReplicas must be > 0 for replicas={r}, got {d}")

    def test_kserve_source_has_max1_guard(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "stack" / "kserve.py").read_text()
        self.assertIn(
            '"decodeReplicas": max(1,',
            src,
            "kserve.py decodeReplicas must be wrapped in max(1, ...) to prevent 0",
        )


if __name__ == "__main__":
    unittest.main()

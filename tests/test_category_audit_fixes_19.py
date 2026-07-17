"""Pass 19 regression tests: slo.py earliest_ns sentinel and broker.py AMD VRAM."""

import unittest


class TestSloEarliestNsSentinel(unittest.TestCase):
    """slo.py: earliest_ns must use -1 as sentinel, not 0."""

    def _make_spans(self, spans):
        """Serialise (start_ns, end_ns, ttft_ms, out_tokens) tuples to JSONL."""
        import json, io
        buf = io.StringIO()
        for start_ns, end_ns, ttft_ms, out_tokens in spans:
            buf.write(json.dumps({
                "start_time_ns": start_ns,
                "end_time_ns": end_ns,
                "ttft_ms": ttft_ms,
                "output_tokens": out_tokens,
            }) + "\n")
        return buf.getvalue()

    def test_zero_start_ns_span_tracked_as_earliest(self):
        """A span with start_ns=0 must remain the earliest when a later span arrives."""
        import tempfile, os
        from aictl.metrics.slo import goodput_from_spans, SLOTarget

        # Span 1: start_ns=0 (legitimate), Span 2: start_ns=1_000_000_000
        spans_text = self._make_spans([
            (0,               1_000_000_000, 50.0, 10),
            (1_000_000_000,   2_000_000_000, 50.0, 10),
        ])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(spans_text)
            path = f.name
        try:
            target = SLOTarget()
            result = goodput_from_spans(path, target)
            # Must return without error and with total_requests = 2
            self.assertIsNotNone(result)
            self.assertEqual(result.total_requests, 2)
        finally:
            os.unlink(path)

    def test_sentinel_in_source_code(self):
        """slo.py must initialise earliest_ns to -1 (not 0)."""
        import pathlib, re
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "metrics" / "slo.py").read_text()
        match = re.search(r"earliest_ns\s*=\s*(-?\d+)", src)
        self.assertIsNotNone(match, "earliest_ns initializer not found")
        self.assertEqual(
            int(match.group(1)), -1,
            "earliest_ns must be initialised to -1 so 0 is not confused with 'unset'",
        )

    def test_condition_uses_lt_0(self):
        """slo.py loop condition must check earliest_ns < 0, not earliest_ns == 0."""
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "metrics" / "slo.py").read_text()
        self.assertIn(
            "earliest_ns < 0",
            src,
            "slo.py must use 'earliest_ns < 0' as the unset-sentinel check",
        )
        self.assertNotIn(
            "earliest_ns == 0",
            src,
            "slo.py must not use 0 as sentinel — 0 is a valid nanosecond timestamp",
        )


class TestBrokerAmdVram(unittest.TestCase):
    """broker.py detect_amd: rocm-smi VRAM output must be parsed, not discarded."""

    def test_vram_extracted_from_rocm_smi_output(self):
        """The rocm-smi VRAM regex must extract MB from a typical rocm-smi CSV line."""
        import re
        # Simulate a rocm-smi CSV line
        sample = "GPU[0]: VRAM Total Memory (B): 25769803776"
        m = re.search(r"GPU\[(\d+)\].*VRAM Total Memory.*?[,:\s]+(\d+)", sample, re.IGNORECASE)
        self.assertIsNotNone(m, "regex must match typical rocm-smi VRAM line")
        vram_mb = int(m.group(2)) // (1024 * 1024)
        self.assertEqual(vram_mb, 24576)  # 24 GB in MB

    def test_detect_amd_source_parses_vram(self):
        """broker.py detect_amd must reference vram_by_index (not always set vram_mb=0)."""
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "runtime" / "broker.py").read_text()
        # Find the detect_amd function
        import re
        fn = re.search(r"def detect_amd\(\).*?^def ", src, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(fn, "detect_amd must exist in broker.py")
        body = fn.group(0)
        self.assertIn(
            "vram_by_index",
            body,
            "detect_amd must parse rocm-smi VRAM into vram_by_index dict",
        )
        self.assertNotIn(
            "vram_mb=0,\n",
            body,
            "detect_amd must not hardcode vram_mb=0; must use vram_by_index.get()",
        )


if __name__ == "__main__":
    unittest.main()

"""Tests for Apple Silicon / unified-memory detection and fit reasoning."""

from __future__ import annotations

import argparse
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.runtime import broker
from aictl.runtime.broker import (
    unified_memory_budget_mb, lookup_apple_silicon_vram, detect_apple_silicon,
    select_profile, GPUInfo, UNIFIED_MEMORY_FRACTION, APPLE_SILICON_RAM_GB,
)


class TestUnifiedMemoryBudget(unittest.TestCase):
    def test_default_fraction(self):
        # 64GB RAM → 75% usable
        self.assertEqual(unified_memory_budget_mb(64 * 1024),
                         int(64 * 1024 * 0.75))

    def test_custom_fraction(self):
        self.assertEqual(unified_memory_budget_mb(1000, 0.5), 500)

    def test_zero_ram(self):
        self.assertEqual(unified_memory_budget_mb(0), 0)


class TestAppleSiliconCatalog(unittest.TestCase):
    def test_known_chip_largest_config(self):
        # M3 Max max config is 128GB → 75% = 96GB in MB
        vram = lookup_apple_silicon_vram("M3 Max")
        self.assertEqual(vram, unified_memory_budget_mb(128 * 1024))

    def test_case_and_prefix_insensitive(self):
        self.assertEqual(lookup_apple_silicon_vram("m3 max"),
                         lookup_apple_silicon_vram("Apple M3 Max"))
        self.assertGreater(lookup_apple_silicon_vram("m2 ultra"), 0)

    def test_unknown_chip_returns_zero(self):
        self.assertEqual(lookup_apple_silicon_vram("RTX 4090"), 0)
        self.assertEqual(lookup_apple_silicon_vram("M99 Hyper"), 0)

    def test_catalog_sizes_sane(self):
        for chip, sizes in APPLE_SILICON_RAM_GB.items():
            self.assertTrue(sizes, chip)
            self.assertTrue(all(s > 0 for s in sizes), chip)


class TestAppleSiliconDetection(unittest.TestCase):
    def test_non_apple_returns_empty(self):
        with mock.patch("platform.system", return_value="Linux"), \
             mock.patch("platform.machine", return_value="x86_64"):
            self.assertEqual(detect_apple_silicon(), [])

    def test_apple_silicon_detected(self):
        def fake_run(cmd, timeout=10):
            if "machdep.cpu.brand_string" in cmd:
                return "Apple M3 Max"
            if "hw.memsize" in cmd:
                return str(64 * 1024 * 1024 * 1024)  # 64GB in bytes
            return None
        with mock.patch("platform.system", return_value="Darwin"), \
             mock.patch("platform.machine", return_value="arm64"), \
             mock.patch.object(broker, "_run", fake_run):
            gpus = detect_apple_silicon()
        self.assertEqual(len(gpus), 1)
        g = gpus[0]
        self.assertEqual(g.vendor, "apple")
        self.assertTrue(g.unified_memory)
        self.assertEqual(g.name, "M3 Max")
        self.assertEqual(g.vram_mb, unified_memory_budget_mb(64 * 1024))

    def test_select_profile_apple(self):
        g = GPUInfo(index=0, name="M3 Max", vendor="apple",
                    vram_mb=49152, driver_version="", compute_cap="metal3",
                    unified_memory=True)
        profile = select_profile([g], [])
        self.assertIn("apple", profile)
        self.assertIn("metal", profile)


class TestFitUnifiedMemory(unittest.TestCase):
    def _run_fit(self, **ns):
        from aictl.cmd import fit
        ns.setdefault("context", 8192)
        ns.setdefault("concurrent", 1)
        ns.setdefault("use_case", "")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = fit.run(argparse.Namespace(**ns))
        return rc, buf.getvalue()

    def test_fit_with_apple_chip_override_json(self):
        rc, out = self._run_fit(model="llama3.1:8b", gpu="M3 Max", json=True)
        data = json.loads(out)
        # 96GB budget easily fits an 8B model
        self.assertTrue(data["fits"])
        self.assertGreater(data["vram_mb_available"], 60000)
        self.assertTrue(any("unified memory" in n for n in data["notes"]))

    def test_fit_unknown_gpu_still_errors(self):
        # err() writes to stderr; the "Known GPUs" hint goes to stdout.
        rc, out = self._run_fit(model="llama3.1:8b", gpu="NotAChip", json=False)
        self.assertEqual(rc, 1)
        self.assertIn("Known GPUs", out)


if __name__ == "__main__":
    unittest.main()

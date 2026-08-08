"""Pass 194: expose KV offload advice through `aictl deploy optimize`.

Items U and V built the advisor and gave it a real measurement, but nothing
surfaced either to users — `enable_kv_offload` was reachable only from Python.
This wires `--kv-offload` / `--host-ram` into the CLI.

Two things needed care beyond flag plumbing:

* Sizing depends on *host* RAM, which the existing auto-detect path never
  needed (it only looked at GPUs) and only ran when `--gpu auto`. Detection is
  now also triggered by `--kv-offload`, and probed once rather than per-use.
* `HardwareProfile.vendor` was defaulting to "nvidia" for every caller,
  including CPU-only hosts, which would have let the advisor recommend
  offloading on a machine with no GPU to offload *from*. Detection now fills
  in the real vendor.

Tests avoid asserting on this machine's actual RAM — they pass `--host-ram`
explicitly wherever the outcome matters.
"""

from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout

from aictl.cmd.deploy import register, run_optimize


def _parse(argv):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    register(sub)
    return parser.parse_args(argv)


def _run(**overrides):
    """Run optimize with explicit args, capturing stdout."""
    ns = argparse.Namespace(
        model="llama-8b", size=8.0, gpu="RTX 4090", gpu_count=1, vram=24000,
        objective="balanced", speculative=False, kv_offload=False, host_ram=0,
        json=False,
    )
    for key, value in overrides.items():
        setattr(ns, key, value)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = run_optimize(ns)
    return rc, buf.getvalue()


class TestCliRegistration(unittest.TestCase):
    def test_flags_are_registered(self):
        ns = _parse(["deploy", "optimize", "m", "--kv-offload", "--host-ram", "65536"])
        self.assertTrue(ns.kv_offload)
        self.assertEqual(ns.host_ram, 65536)

    def test_defaults_are_off(self):
        ns = _parse(["deploy", "optimize", "m"])
        self.assertFalse(ns.kv_offload)
        self.assertEqual(ns.host_ram, 0)


class TestOptimizeOutput(unittest.TestCase):
    def test_default_emits_no_offload_flag(self):
        rc, out = _run()
        self.assertEqual(rc, 0)
        self.assertNotIn("kv-transfer-config", out)
        self.assertNotIn("KV offload", out)

    def test_enabled_with_ample_host_ram_emits_the_flag(self):
        rc, out = _run(kv_offload=True, host_ram=128 * 1024)
        self.assertEqual(rc, 0)
        self.assertIn("--kv-transfer-config", out)
        self.assertIn("OffloadingConnector", out)

    def test_declines_with_a_visible_reason_on_small_host(self):
        rc, out = _run(kv_offload=True, host_ram=4096)
        self.assertEqual(rc, 0)   # advisory, not an error
        self.assertNotIn("kv-transfer-config", out)
        self.assertIn("not applied", out)

    def test_cpu_only_host_declines(self):
        # vendor is derived from detection; an explicit CPU gpu name with no
        # VRAM must not produce an offload recommendation.
        rc, out = _run(kv_offload=True, host_ram=128 * 1024, gpu="CPU", vram=0)
        self.assertEqual(rc, 0)
        # Either it declines outright, or (if treated as a GPU profile) it must
        # at minimum not claim to offload from a device that isn't there.
        if "kv-transfer-config" in out:
            self.fail("recommended offloading on a CPU-only profile")

    def test_json_output_stays_valid(self):
        rc, out = _run(kv_offload=True, host_ram=128 * 1024, json=True)
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertTrue(any("kv-transfer-config" in f for f in payload["flags"]))

    def test_json_output_unchanged_when_disabled(self):
        _, on = _run(kv_offload=False, host_ram=128 * 1024, json=True)
        payload = json.loads(on)
        self.assertFalse(any("kv-transfer-config" in f for f in payload["flags"]))

    def test_explicit_host_ram_overrides_detection(self):
        # A caller stating their host size must not be second-guessed by a
        # probe of the machine generating the flags (which is often not the
        # machine that will run the engine).
        _, small = _run(kv_offload=True, host_ram=4096)
        _, large = _run(kv_offload=True, host_ram=256 * 1024)
        self.assertIn("not applied", small)
        self.assertIn("--kv-transfer-config", large)

    def test_generated_command_is_still_runnable_shape(self):
        _, out = _run(kv_offload=True, host_ram=128 * 1024)
        self.assertIn("vllm serve", out)
        # The JSON payload must stay single-quoted inside the command so the
        # braces survive a shell.
        self.assertIn("--kv-transfer-config '{", out)

    def test_missing_new_attrs_do_not_crash(self):
        # Namespaces built by older callers/tests lack kv_offload/host_ram.
        ns = argparse.Namespace(model="llama-8b", size=8.0, gpu="RTX 4090",
                                gpu_count=1, vram=24000, objective="balanced")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_optimize(ns)
        self.assertEqual(rc, 0)
        self.assertNotIn("kv-transfer-config", buf.getvalue())


class TestVendorInference(unittest.TestCase):
    """Regression guard: an explicitly named GPU used to keep the "nvidia"
    default, which let `--gpu CPU` recommend offloading from a device that
    wasn't there. Caught by test_cpu_only_host_declines."""

    def test_cpu_names_map_to_cpu(self):
        from aictl.cmd.deploy import _infer_vendor
        for name in ("CPU", "cpu", "none", "", "  "):
            self.assertEqual(_infer_vendor(name, 0), "cpu", name)

    def test_amd_names(self):
        from aictl.cmd.deploy import _infer_vendor
        for name in ("MI300X", "Radeon Pro W7900", "Instinct MI250"):
            self.assertEqual(_infer_vendor(name, 24000), "amd", name)

    def test_intel_names(self):
        from aictl.cmd.deploy import _infer_vendor
        for name in ("Arc A770", "Gaudi 3"):
            self.assertEqual(_infer_vendor(name, 16000), "intel", name)

    def test_unknown_gpu_keeps_the_historical_default(self):
        # Better to keep advising for a GPU missing from the tables than to
        # silently disable features for it.
        from aictl.cmd.deploy import _infer_vendor
        self.assertEqual(_infer_vendor("H100", 80000), "nvidia")
        self.assertEqual(_infer_vendor("SomeFutureGPU", 48000), "nvidia")

    def test_cpu_profile_reaches_the_advisor_as_cpu(self):
        rc, out = _run(kv_offload=True, host_ram=256 * 1024, gpu="CPU", vram=0)
        self.assertEqual(rc, 0)
        self.assertIn("not applied", out)


class TestDetectionIsNotOverused(unittest.TestCase):
    def test_no_hardware_probe_when_gpu_and_ram_are_given(self):
        # full_detect() shells out to nvidia-smi/rocm-smi; with everything
        # specified there is nothing to discover and it must not run.
        import aictl.runtime.broker as broker

        calls = []
        original = broker.full_detect
        broker.full_detect = lambda: (calls.append(1), original())[1]
        try:
            _run(kv_offload=True, host_ram=128 * 1024, gpu="RTX 4090", vram=24000)
        finally:
            broker.full_detect = original
        self.assertEqual(calls, [])

    def test_probe_runs_once_when_host_ram_is_needed(self):
        import aictl.runtime.broker as broker

        calls = []
        original = broker.full_detect
        broker.full_detect = lambda: (calls.append(1), original())[1]
        try:
            _run(kv_offload=True, host_ram=0, gpu="auto")
        finally:
            broker.full_detect = original
        self.assertEqual(len(calls), 1, "hardware should be probed exactly once")


if __name__ == "__main__":
    unittest.main()

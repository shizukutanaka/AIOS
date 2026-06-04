"""Tests for Phase 8: MIG planner, audit log, API keys, image builder."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.runtime.mig import (
    plan_partitions, generate_mig_commands, get_gpu_type,
    ModelRequirement, MIG_PROFILES, PartitionPlan,
)
from aictl.core.audit import AuditLog, AuditEntry, audit
from aictl.core.apikeys import KeyManager, APIKey
from aictl.deploy.imagebuilder import list_formats, SUPPORTED_FORMATS, BuildResult


class TestMIGPlanner(unittest.TestCase):
    def test_get_gpu_type(self):
        self.assertEqual(get_gpu_type("NVIDIA A100-SXM4-80GB"), "A100-80GB")
        self.assertEqual(get_gpu_type("NVIDIA H100 80GB"), "H100-80GB")
        self.assertEqual(get_gpu_type("NVIDIA H200"), "H200-141GB")
        self.assertEqual(get_gpu_type("RTX 4090"), "")

    def test_plan_single_model(self):
        models = [ModelRequirement("llama3", 16)]
        plan = plan_partitions("NVIDIA A100-SXM4-80GB", 0, models)
        self.assertGreater(len(plan.partitions), 0)
        self.assertGreater(plan.utilization, 0)

    def test_plan_multiple_models(self):
        models = [
            ModelRequirement("llama3", 16),
            ModelRequirement("embedding", 2),
            ModelRequirement("whisper", 4),
        ]
        plan = plan_partitions("NVIDIA A100-SXM4-80GB", 0, models)
        allocated = [p for p in plan.partitions if p.get("profile") != "none"]
        self.assertGreater(len(allocated), 0)

    def test_plan_too_large_model(self):
        models = [ModelRequirement("huge-model", 200)]
        plan = plan_partitions("NVIDIA A100-SXM4-80GB", 0, models)
        unfit = [p for p in plan.partitions if p.get("status") == "does not fit"]
        self.assertEqual(len(unfit), 1)

    def test_plan_unknown_gpu(self):
        plan = plan_partitions("RTX 4090", 0, [ModelRequirement("m", 8)])
        self.assertTrue(any("error" in p for p in plan.partitions))

    def test_generate_commands(self):
        models = [ModelRequirement("llama3", 16)]
        plan = plan_partitions("NVIDIA H100 80GB", 0, models)
        cmds = generate_mig_commands(plan)
        self.assertGreater(len(cmds), 0)
        self.assertTrue(any("nvidia-smi" in c for c in cmds))

    def test_profiles_exist(self):
        for gpu_type, profiles in MIG_PROFILES.items():
            self.assertGreater(len(profiles), 0)
            for p in profiles:
                self.assertGreater(p.memory_gb, 0)
                self.assertGreater(p.gpu_instances, 0)


class TestAuditLog(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.log = AuditLog(self.tmp)

    def test_write_read(self):
        self.log.write(AuditEntry(event="test.event", resource="model1", action="create"))
        entries = self.log.read(n=10)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].event, "test.event")

    def test_filter(self):
        self.log.write(AuditEntry(event="a", resource="r1"))
        self.log.write(AuditEntry(event="b", resource="r2"))
        self.log.write(AuditEntry(event="a", resource="r3"))
        entries = self.log.read(n=10, event_filter="a")
        self.assertEqual(len(entries), 2)

    def test_empty_log(self):
        entries = self.log.read()
        self.assertEqual(len(entries), 0)

    def test_convenience_function(self):
        audit("model.loaded", resource="llama3", action="create",
              state_dir=self.tmp, digest="sha256:abc")
        log = AuditLog(self.tmp)
        entries = log.read(n=1)
        self.assertEqual(len(entries), 1)


class TestAPIKeyManager(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mgr = KeyManager(self.tmp)

    def test_generate_and_validate(self):
        raw, key = self.mgr.generate_key("test-key")
        self.assertTrue(raw.startswith("aios-"))
        self.assertTrue(key.active)

        valid, reason, found = self.mgr.validate(raw)
        self.assertTrue(valid)
        self.assertEqual(found.name, "test-key")

    def test_invalid_key(self):
        valid, reason, _ = self.mgr.validate("invalid-key")
        self.assertFalse(valid)

    def test_revoke(self):
        raw, key = self.mgr.generate_key("revoke-test")
        self.assertTrue(self.mgr.revoke(key.key_id))

        valid, reason, _ = self.mgr.validate(raw)
        self.assertFalse(valid)
        self.assertIn("revoked", reason.lower())

    def test_rate_limit(self):
        raw, key = self.mgr.generate_key("rate-test", rate_limit_rpm=2)
        _, _, k = self.mgr.validate(raw)

        ok1, _ = self.mgr.check_rate_limit(k)
        self.assertTrue(ok1)
        ok2, _ = self.mgr.check_rate_limit(k)
        self.assertTrue(ok2)
        ok3, msg = self.mgr.check_rate_limit(k)
        self.assertFalse(ok3)
        self.assertIn("exceeded", msg.lower())

    def test_list_keys(self):
        self.mgr.generate_key("key1")
        self.mgr.generate_key("key2")
        keys = self.mgr.list_keys()
        self.assertEqual(len(keys), 2)

    def test_usage_tracking(self):
        raw, key = self.mgr.generate_key("usage-test")
        self.mgr.record_usage(key.key_id, tokens=100)
        self.mgr.record_usage(key.key_id, tokens=50)
        keys = self.mgr.list_keys()
        k = [k for k in keys if k["key_id"] == key.key_id][0]
        self.assertEqual(k["total_requests"], 2)
        self.assertEqual(k["total_tokens"], 150)


class TestImageBuilder(unittest.TestCase):
    def test_supported_formats(self):
        self.assertIn("qcow2", SUPPORTED_FORMATS)
        self.assertIn("iso", SUPPORTED_FORMATS)
        self.assertIn("ami", SUPPORTED_FORMATS)

    def test_list_formats(self):
        fmts = list_formats()
        self.assertGreater(len(fmts), 5)
        names = [f["format"] for f in fmts]
        self.assertIn("qcow2", names)

    def test_build_result_defaults(self):
        r = BuildResult()
        self.assertFalse(r.success)


class TestPhase8CLI(unittest.TestCase):
    def test_mig_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["mig", "plan", "--models", "llama:16", "embed:2"])
        self.assertEqual(args.mig_cmd, "plan")

    def test_audit_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["audit", "-n", "50", "--event", "model.loaded"])
        self.assertEqual(args.lines, 50)
        self.assertEqual(args.event, "model.loaded")

    def test_apikey_create_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["apikey", "create", "mykey", "--rpm", "100"])
        self.assertEqual(args.name, "mykey")
        self.assertEqual(args.rpm, 100)

    def test_image_formats_parser(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["image", "formats"])
        self.assertEqual(args.image_cmd, "formats")

    def test_all_27_commands(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        simple = ["init", "doctor", "ps", "serve", "status", "setup",
                   "recommend", "proxy", "net", "watch"]
        for cmd in simple:
            args = p.parse_args([cmd])
            self.assertEqual(args.command, cmd, f"Failed: {cmd}")


if __name__ == "__main__":
    unittest.main()

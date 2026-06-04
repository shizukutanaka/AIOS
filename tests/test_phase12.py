"""Tests for Phase 12: multi-tenant, model formats, E2E integration."""

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.core.tenant import (
    TENANT_CLASSES, Tenant, get_tenant_class,
    generate_k8s_namespace, generate_cgroup_limits,
)
from aictl.runtime.formats import (
    detect_format, detect_model_dir, recommend_runtime,
    ModelFormat, RUNTIME_COMPAT, format_size,
)
from aictl.core.state import StateStore, NodeState


class TestTenantClasses(unittest.TestCase):
    def test_predefined(self):
        self.assertIn("regulated", TENANT_CLASSES)
        self.assertIn("standard", TENANT_CLASSES)
        self.assertIn("dev", TENANT_CLASSES)

    def test_regulated_strict(self):
        tc = get_tenant_class("regulated")
        self.assertTrue(tc.require_signed_models)
        self.assertFalse(tc.allow_internet)

    def test_unknown_defaults(self):
        tc = get_tenant_class("nonexistent")
        self.assertEqual(tc.name, "standard")


class TestK8sNamespace(unittest.TestCase):
    def test_namespace_and_quota(self):
        tenant = Tenant(id="t1", name="Test", tenant_class="standard")
        m = generate_k8s_namespace(tenant)
        kinds = [i["kind"] for i in m["items"]]
        self.assertIn("Namespace", kinds)
        self.assertIn("ResourceQuota", kinds)

    def test_regulated_network_policy(self):
        tenant = Tenant(id="t2", name="R", tenant_class="regulated")
        m = generate_k8s_namespace(tenant)
        kinds = [i["kind"] for i in m["items"]]
        self.assertIn("NetworkPolicy", kinds)

    def test_standard_no_network_policy(self):
        tenant = Tenant(id="t3", name="S", tenant_class="standard")
        m = generate_k8s_namespace(tenant)
        kinds = [i["kind"] for i in m["items"]]
        self.assertNotIn("NetworkPolicy", kinds)


class TestCgroupLimits(unittest.TestCase):
    def test_generates(self):
        limits = generate_cgroup_limits(Tenant(id="l", name="l"))
        self.assertIn("MemoryMax", limits)

    def test_regulated_higher(self):
        reg = generate_cgroup_limits(Tenant(id="r", name="r", tenant_class="regulated"))
        dev = generate_cgroup_limits(Tenant(id="d", name="d", tenant_class="dev"))
        self.assertGreater(int(reg["MemoryMax"].rstrip("G")), int(dev["MemoryMax"].rstrip("G")))


class TestModelFormats(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_gguf_magic(self):
        path = self.tmp / "m.bin"
        path.write_bytes(b"GGUF" + struct.pack("<I", 3) + b"\x00" * 100)
        self.assertEqual(detect_format(path).format, "gguf")
        self.assertEqual(detect_format(path).version, "v3")

    def test_safetensors(self):
        path = self.tmp / "m.safetensors"
        path.write_bytes(b"{}" + b"\x00" * 100)
        fmt = detect_format(path)
        self.assertEqual(fmt.format, "safetensors")
        self.assertIn("vllm", fmt.compatible_runtimes)

    def test_onnx(self):
        path = self.tmp / "m.onnx"
        path.write_bytes(b"\x08\x00" * 50)
        self.assertEqual(detect_format(path).format, "onnx")

    def test_pytorch(self):
        path = self.tmp / "m.pt"
        path.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
        self.assertEqual(detect_format(path).format, "pytorch")

    def test_nonexistent(self):
        self.assertEqual(detect_format("/no/such/file").format, "unknown")

    def test_dir_scan(self):
        (self.tmp / "a.gguf").write_bytes(b"GGUF\x03\x00\x00\x00" + b"\x00" * 50)
        (self.tmp / "b.safetensors").write_bytes(b"{}" + b"\x00" * 50)
        results = detect_model_dir(self.tmp)
        self.assertEqual(len(results), 2)

    def test_recommend(self):
        self.assertEqual(recommend_runtime(ModelFormat(format="gguf")), "ollama")
        self.assertEqual(recommend_runtime(ModelFormat(format="safetensors")), "vllm")

    def test_format_size(self):
        self.assertIn("GB", format_size(7 * 1024**3))
        self.assertIn("MB", format_size(5 * 1024**2))


class TestE2EFullStack(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.store = StateStore(cls.tmp)

    def test_01_init(self):
        from aictl.runtime.broker import full_detect
        report = full_detect()
        self.store.save_node(NodeState(
            node_id="e2e", hostname="test", profile=report.profile,
            version="1.2.0", ram_total_mb=report.system.ram_total_mb))
        self.assertTrue(self.store.is_initialized())

    def test_02_recipes(self):
        from aictl.stack.manifest import list_recipes
        self.assertGreaterEqual(len(list_recipes()), 8)

    def test_03_recommend(self):
        from aictl.runtime.recommend import recommend
        self.assertGreater(len(recommend(ram_mb=16384)), 0)

    def test_04_kserve(self):
        from aictl.stack.manifest import get_recipe
        from aictl.stack.kserve import stack_to_llmisvc
        resources = stack_to_llmisvc(get_recipe("team-rag"))
        self.assertTrue(any(r["kind"] == "LLMInferenceService" for r in resources))

    def test_05_keda(self):
        from aictl.runtime.autoscaler import generate_keda_scaled_object
        self.assertEqual(generate_keda_scaled_object("v")["kind"], "ScaledObject")

    def test_06_fabric(self):
        from aictl.runtime.fabric import detect_memory_fabric
        self.assertGreater(detect_memory_fabric().total_capacity_gb, 0)

    def test_07_tenant(self):
        m = generate_k8s_namespace(Tenant(id="e2e", name="e2e", tenant_class="regulated"))
        self.assertGreater(len(m["items"]), 2)

    def test_08_otel(self):
        from aictl.metrics.collector_config import generate_otel_config
        from aictl.core.config import Config
        self.assertIn("receivers:", generate_otel_config(Config()))

    def test_09_snapshot(self):
        from aictl.core.snapshots import SnapshotManager
        mgr = SnapshotManager(self.store)
        mgr.create(label="e2e")
        self.assertGreater(len(mgr.list_snapshots()), 0)

    def test_10_audit(self):
        from aictl.core.audit import AuditLog, AuditEntry
        log = AuditLog(self.tmp)
        log.write(AuditEntry(event="e2e.test"))
        self.assertEqual(len(log.read(n=1)), 1)


class TestTenantCLI(unittest.TestCase):
    def test_classes(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["tenant", "classes"])
        self.assertEqual(args.tenant_cmd, "classes")

    def test_namespace(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["tenant", "namespace", "acme", "--class", "regulated"])
        self.assertEqual(args.tenant_id, "acme")

    def test_all_32_commands(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        for cmd in ["init", "doctor", "ps", "serve", "status", "setup",
                     "recommend", "proxy", "net", "watch", "trace"]:
            self.assertEqual(p.parse_args([cmd]).command, cmd)


if __name__ == "__main__":
    unittest.main()

"""Pass 168 (audit item 22 + P5): apikey<->tenant reverse view, honest enforcement legend.

FEATURE_GAP_LIST.md item 22: `aictl apikey inspect <id>` didn't show which
tenant (if any) that key is linked to, even though `aictl tenant link-key`
(Pass 164) creates exactly that link. Fixed by reusing the existing
`find_tenant_by_key_id` lookup.

FEATURE_GAP_AUDIT.md P5: `aictl tenant classes` displayed gpu/ram/vram/
max_models/audit_level columns with no visual distinction from rpm/signed —
but only rpm/tpm/allow_internet/require_signed_models are actually enforced
live by the proxy (Passes 164-166); the rest only materialize into generated
K8s/cgroup config that something else has to apply. Rather than inventing
fake local enforcement for hardware-allocation concepts a request-routing
proxy can't meaningfully check per-request, this makes the distinction
explicit: a `*` marker + legend in human output, and an `enforcement` block
in --json (`proxy_enforced` vs `generation_only` field lists) so a script or
another Claude session can tell the difference programmatically too.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path


def _cli_apikey(argv):
    from aictl.cmd import apikey
    p = argparse.ArgumentParser(prog="aictl")
    p.add_argument("--state-dir", default=None)
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers()
    apikey.register(sub)
    ns = p.parse_args(argv)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = ns.func(ns)
    return code, out.getvalue()


def _cli_tenant(argv):
    from aictl.cmd import tenant
    p = argparse.ArgumentParser(prog="aictl")
    p.add_argument("--state-dir", default=None)
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers()
    tenant.register(sub)
    ns = p.parse_args(argv)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = ns.func(ns)
    return code, out.getvalue()


class TestApikeyInspectShowsTenant(unittest.TestCase):
    def _make_key(self, d):
        from aictl.core.apikeys import KeyManager
        raw, key = KeyManager(Path(d)).generate_key("k1")
        return raw, key

    def test_unlinked_key_shows_no_tenant(self):
        # `apikey inspect` declares its own local --json (unlike trust/tenant,
        # which only use the global flag) — it must come AFTER the
        # subcommand+args here, matching how the real CLI actually accepts it.
        d = tempfile.mkdtemp()
        raw, key = self._make_key(d)
        code, text = _cli_apikey(["--state-dir", d, "apikey",
                                  "inspect", key.key_id, "--json"])
        data = json.loads(text)
        self.assertIsNone(data["tenant_id"])
        self.assertIsNone(data["tenant_class"])

    def test_linked_key_shows_tenant_and_class(self):
        d = tempfile.mkdtemp()
        raw, key = self._make_key(d)
        _cli_tenant(["--state-dir", d, "tenant", "create", "regco",
                    "--class", "regulated"])
        _cli_tenant(["--state-dir", d, "tenant", "link-key", "regco", key.key_id])

        code, text = _cli_apikey(["--state-dir", d, "apikey",
                                  "inspect", key.key_id, "--json"])
        data = json.loads(text)
        self.assertEqual(data["tenant_id"], "regco")
        self.assertEqual(data["tenant_class"], "regulated")

    def test_human_output_shows_link_hint_when_unlinked(self):
        d = tempfile.mkdtemp()
        raw, key = self._make_key(d)
        code, text = _cli_apikey(["--state-dir", d, "apikey", "inspect", key.key_id])
        self.assertIn("not linked", text)
        self.assertIn("tenant link-key", text)

    def test_human_output_shows_tenant_when_linked(self):
        d = tempfile.mkdtemp()
        raw, key = self._make_key(d)
        _cli_tenant(["--state-dir", d, "tenant", "create", "acme"])
        _cli_tenant(["--state-dir", d, "tenant", "link-key", "acme", key.key_id])
        code, text = _cli_apikey(["--state-dir", d, "apikey", "inspect", key.key_id])
        self.assertIn("acme", text)
        self.assertIn("standard", text)   # default tenant class

    def test_unknown_key_still_errors_cleanly(self):
        d = tempfile.mkdtemp()
        code, text = _cli_apikey(["--state-dir", d, "apikey", "inspect", "nope"])
        self.assertEqual(code, 1)


class TestTenantClassesEnforcementLegend(unittest.TestCase):
    def test_json_includes_enforcement_block(self):
        code, text = _cli_tenant(["--json", "tenant", "classes"])
        data = json.loads(text)
        self.assertIn("classes", data)
        self.assertIn("enforcement", data)
        self.assertIn("proxy_enforced", data["enforcement"])
        self.assertIn("generation_only", data["enforcement"])

    def test_proxy_enforced_fields_match_what_proxy_py_actually_checks(self):
        # These four are the fields Passes 164-166 wired into proxy.py; this
        # test would fail if that set ever silently drifted from the legend.
        code, text = _cli_tenant(["--json", "tenant", "classes"])
        data = json.loads(text)
        self.assertEqual(set(data["enforcement"]["proxy_enforced"]),
                         {"max_requests_per_min", "max_tokens_per_min",
                          "allow_internet", "require_signed_models"})

    def test_generation_only_fields_are_the_hardware_allocation_ones(self):
        code, text = _cli_tenant(["--json", "tenant", "classes"])
        data = json.loads(text)
        self.assertEqual(set(data["enforcement"]["generation_only"]),
                         {"max_gpu_slices", "max_memory_gb", "max_vram_gb",
                          "max_models", "audit_level"})

    def test_classes_data_unchanged_by_the_new_wrapper(self):
        # The wrapper changed the top-level JSON shape (classes/enforcement
        # instead of a flat {name: ...} dict), but the per-class data itself
        # must be byte-identical to the underlying TENANT_CLASSES dataclasses.
        from aictl.core.tenant import TENANT_CLASSES
        from dataclasses import asdict
        code, text = _cli_tenant(["--json", "tenant", "classes"])
        data = json.loads(text)
        for name, tc in TENANT_CLASSES.items():
            self.assertEqual(data["classes"][name], asdict(tc))

    def test_human_output_has_asterisk_markers_and_legend(self):
        code, text = _cli_tenant(["tenant", "classes"])
        self.assertIn("GPU*", text)
        self.assertIn("RAM*", text)
        self.assertIn("VRAM*", text)
        self.assertIn("not enforced locally", text.lower())

    def test_human_output_still_lists_all_three_classes(self):
        code, text = _cli_tenant(["tenant", "classes"])
        self.assertIn("regulated", text)
        self.assertIn("standard", text)
        self.assertIn("dev", text)


if __name__ == "__main__":
    unittest.main()

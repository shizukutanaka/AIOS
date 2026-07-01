"""Pass 165 (過不足 follow-on): tenant-class `allow_internet` also had no teeth.

ソクラテス式問答法で過不足の機能を考える continued: Pass 164 fixed
`max_requests_per_min`/`max_tokens_per_min` having no runtime consumer. Natural
follow-up question: are `TenantClass`'s OTHER fields (`allow_internet`,
`require_signed_models`, `max_models`) suffering the same fate? A repo-wide grep
proved `allow_internet` was referenced NOWHERE outside its own dataclass
definition — meaning a "regulated" tenant (`allow_internet=False` by design, for
air-gapped/regulated workloads) would, if local engines went down and cloud
fallback was globally enabled, have their request silently routed to an
external cloud API anyway. This is a genuine data-exfiltration-shaped gap, not
just a cosmetic one: the tenant class exists specifically to promise "this
workload never leaves the building."

Fix: `ProxyHandler._tenant_disallows_internet()` resolves the requesting key's
linked tenant (same `find_tenant_by_key_id` lookup wired in Pass 164) and checks
`get_tenant_class(...).allow_internet`. `_try_cloud_fallback` now checks this
FIRST, before even loading the fallback config, and audits a
`proxy.cloud_fallback_blocked` event on refusal. Unlinked keys and
internet-allowed tenant classes are completely unaffected (opt-in layer, no
behavior change for anyone not using `tenant link-key`).

(`require_signed_models` and `max_models` remain unenforced — noted as a larger,
separate undertaking: the proxy has no model-level trust-checking hook at all
today, for any tenant, so wiring one meaningfully needs more design than a
single-field fix.)
"""

from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock


class _ProxyStub:
    """Binds the real ProxyHandler methods to a lightweight, testable stub."""

    from aictl.daemon.proxy import ProxyHandler as _H
    _tenant_disallows_internet = _H._tenant_disallows_internet
    _try_cloud_fallback = _H._try_cloud_fallback
    _meter_tokens = _H._meter_tokens
    _error = _H._error
    _json = _H._json

    def __init__(self, state_dir, bearer=""):
        self.store = type("S", (), {"dir": Path(state_dir)})()
        self.headers = {}
        if bearer:
            self.headers["Authorization"] = f"Bearer {bearer}"
        self.wfile = io.BytesIO()

    def send_response(self, *a, **k):
        pass

    def send_header(self, *a, **k):
        pass

    def end_headers(self):
        pass


def _make_key(state_dir, name="k1"):
    from aictl.core.apikeys import KeyManager
    mgr = KeyManager(Path(state_dir))
    return mgr.generate_key(name)


def _link_tenant(state_dir, tenant_id, tenant_class, key_id):
    from aictl.cmd import tenant as tenant_cmd
    p = argparse.ArgumentParser(prog="aictl")
    p.add_argument("--state-dir", default=None)
    sub = p.add_subparsers()
    tenant_cmd.register(sub)
    for argv in (["tenant", "create", tenant_id, "--class", tenant_class],
                ["tenant", "link-key", tenant_id, key_id]):
        ns = p.parse_args(["--state-dir", str(state_dir)] + argv)
        ns.func(ns)


class TestTenantDisallowsInternet(unittest.TestCase):
    def test_regulated_tenant_disallows_internet(self):
        d = tempfile.mkdtemp()
        raw, key = _make_key(d)
        _link_tenant(d, "regco", "regulated", key.key_id)
        stub = _ProxyStub(d, bearer=raw)
        self.assertTrue(stub._tenant_disallows_internet())

    def test_standard_tenant_allows_internet(self):
        d = tempfile.mkdtemp()
        raw, key = _make_key(d)
        _link_tenant(d, "std", "standard", key.key_id)
        stub = _ProxyStub(d, bearer=raw)
        self.assertFalse(stub._tenant_disallows_internet())

    def test_unlinked_key_allows_internet(self):
        d = tempfile.mkdtemp()
        raw, key = _make_key(d)
        stub = _ProxyStub(d, bearer=raw)
        self.assertFalse(stub._tenant_disallows_internet())

    def test_no_auth_header_allows_internet(self):
        d = tempfile.mkdtemp()
        stub = _ProxyStub(d)
        self.assertFalse(stub._tenant_disallows_internet())

    def test_block_writes_audit_entry(self):
        d = tempfile.mkdtemp()
        raw, key = _make_key(d)
        _link_tenant(d, "regco", "regulated", key.key_id)
        stub = _ProxyStub(d, bearer=raw)
        stub._tenant_disallows_internet()

        from aictl.core.audit import AuditLog
        entries = AuditLog(Path(d)).read(n=10)
        blocked = [e for e in entries if e.event == "proxy.cloud_fallback_blocked"]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0].resource, "regco")


class TestCloudFallbackRespectsAllowInternet(unittest.TestCase):
    def _fallback(self, state_dir, bearer):
        stub = _ProxyStub(state_dir, bearer=bearer)
        with patch("aictl.runtime.fallback.load_fallback_config") as lfc:
            with patch("aictl.runtime.fallback.cloud_completion") as cc:
                lfc.return_value = MagicMock(enabled=True)
                cc.return_value = {"choices": []}
                result = stub._try_cloud_fallback({"model": "x", "messages": []})
                return result, cc.called

    def test_regulated_tenant_never_reaches_cloud_completion(self):
        d = tempfile.mkdtemp()
        raw, key = _make_key(d)
        _link_tenant(d, "regco", "regulated", key.key_id)
        result, cc_called = self._fallback(d, raw)
        self.assertFalse(result)
        self.assertFalse(cc_called)   # the critical assertion: never even tried

    def test_standard_tenant_still_gets_fallback(self):
        d = tempfile.mkdtemp()
        raw, key = _make_key(d)
        _link_tenant(d, "std", "standard", key.key_id)
        result, cc_called = self._fallback(d, raw)
        self.assertTrue(result)
        self.assertTrue(cc_called)

    def test_unlinked_key_unaffected_by_this_pass(self):
        d = tempfile.mkdtemp()
        raw, key = _make_key(d)
        result, cc_called = self._fallback(d, raw)
        self.assertTrue(result)
        self.assertTrue(cc_called)

    def test_dev_class_also_allows_internet(self):
        d = tempfile.mkdtemp()
        raw, key = _make_key(d)
        _link_tenant(d, "devteam", "dev", key.key_id)
        result, cc_called = self._fallback(d, raw)
        self.assertTrue(cc_called)


if __name__ == "__main__":
    unittest.main()

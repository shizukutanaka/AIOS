"""Pass 164 (過不足): tenant-class rate limiting existed on paper, never enforced.

ソクラテス式問答法で過不足の機能を考える surfaced a significant 不足: `core/tenant.py`
defines `TenantRateLimiter`/`get_rate_limiter()` with a module-level singleton
comment literally claiming "used by proxy and daemon for enforcement", and the
module's own docstring lists "Rate limiting per tenant via API keys" as a
provided capability. A repo-wide grep proved neither was ever imported or
called from `daemon/proxy.py`, `daemon/aiosd.py`, or anywhere outside
`core/tenant.py` itself — the class was dead code. Worse, the persisted tenant
registry (`cmd/tenant.py`) never even populated `Tenant.api_key_ids`, so there
was no way to know which API key belonged to which tenant in the first place.
`aictl tenant create` provisioned metadata with zero runtime teeth.

Fix, closing the whole chain:
  - `core.tenant.registry_path`/`load_registry` — single source of truth for
    where tenants live and how to load them defensively (V7: a non-dict root
    degrades to {} rather than crashing a live request in the proxy's hot
    path). `cmd/tenant.py`'s private duplicates now delegate to these.
  - `core.tenant.find_tenant_by_key_id` — resolves an API key id to its tenant.
  - `aictl tenant link-key` / `unlink-key` — new CLI subcommands to actually
    populate `api_key_ids` on a tenant record.
  - `daemon/proxy.py._check_auth` now checks the linked tenant's class rate
    limit (in ADDITION to the existing per-key limit) BEFORE routing a
    request; `_meter_tokens` records the real token count into the same
    tenant's window AFTER the response completes — one check()+record() pair
    per request, matching TenantRateLimiter's own documented two-phase
    contract. An unlinked key is completely unaffected (opt-in layer).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path


# ── core.tenant registry/lookup ────────────────────────────────────────────

class TestRegistryHelpers(unittest.TestCase):
    def test_registry_path_uses_state_dir(self):
        from aictl.core.tenant import registry_path
        d = Path(tempfile.mkdtemp())
        self.assertEqual(registry_path(d), d / "tenants.json")

    def test_load_registry_missing_file_is_empty(self):
        from aictl.core.tenant import load_registry
        d = Path(tempfile.mkdtemp())
        self.assertEqual(load_registry(d / "nope.json"), {})

    def test_load_registry_rejects_non_dict_root(self):
        # V7: a corrupt registry must never crash a live request.
        from aictl.core.tenant import load_registry
        d = Path(tempfile.mkdtemp())
        p = d / "tenants.json"
        p.write_text("[1, 2, 3]")
        self.assertEqual(load_registry(p), {})

    def test_load_registry_rejects_malformed_json(self):
        from aictl.core.tenant import load_registry
        d = Path(tempfile.mkdtemp())
        p = d / "tenants.json"
        p.write_text("{not json")
        self.assertEqual(load_registry(p), {})


class TestFindTenantByKeyId(unittest.TestCase):
    def test_finds_linked_tenant(self):
        from aictl.core.tenant import registry_path, find_tenant_by_key_id
        from aictl.core.atomicio import atomic_write_text
        d = Path(tempfile.mkdtemp())
        atomic_write_text(registry_path(d), json.dumps({
            "acme": {"id": "acme", "tenant_class": "regulated",
                     "api_key_ids": ["key123"]},
        }))
        tenant = find_tenant_by_key_id(d, "key123")
        self.assertIsNotNone(tenant)
        self.assertEqual(tenant["id"], "acme")

    def test_unlinked_key_returns_none(self):
        from aictl.core.tenant import registry_path, find_tenant_by_key_id
        from aictl.core.atomicio import atomic_write_text
        d = Path(tempfile.mkdtemp())
        atomic_write_text(registry_path(d), json.dumps({
            "acme": {"id": "acme", "tenant_class": "standard", "api_key_ids": []},
        }))
        self.assertIsNone(find_tenant_by_key_id(d, "unknown-key"))

    def test_empty_key_id_returns_none(self):
        from aictl.core.tenant import find_tenant_by_key_id
        self.assertIsNone(find_tenant_by_key_id(Path(tempfile.mkdtemp()), ""))

    def test_no_registry_returns_none(self):
        from aictl.core.tenant import find_tenant_by_key_id
        self.assertIsNone(find_tenant_by_key_id(Path(tempfile.mkdtemp()), "anything"))


# ── CLI: link-key / unlink-key ──────────────────────────────────────────────

class TestLinkUnlinkCli(unittest.TestCase):
    def _cli(self, argv):
        # --json is a global flag (like --state-dir) that must precede the
        # subcommand in plain argparse, matching the real __main__.py parser.
        from aictl.cmd import tenant
        p = argparse.ArgumentParser(prog="aictl")
        p.add_argument("--state-dir", default=None)
        p.add_argument("--json", action="store_true")
        sub = p.add_subparsers()
        tenant.register(sub)
        ns = p.parse_args(argv)
        out, errbuf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out):
            with contextlib.redirect_stderr(errbuf):
                code = ns.func(ns)
        return code, out.getvalue() + errbuf.getvalue()

    def test_link_key_persists(self):
        d = tempfile.mkdtemp()
        self._cli(["--state-dir", d, "tenant", "create", "acme"])
        code, _ = self._cli(["--state-dir", d, "tenant", "link-key", "acme", "key1"])
        self.assertEqual(code, 0)
        from aictl.core.tenant import find_tenant_by_key_id
        self.assertEqual(find_tenant_by_key_id(Path(d), "key1")["id"], "acme")

    def test_link_key_idempotent(self):
        d = tempfile.mkdtemp()
        self._cli(["--state-dir", d, "tenant", "create", "acme"])
        self._cli(["--state-dir", d, "tenant", "link-key", "acme", "key1"])
        code, text = self._cli(["--state-dir", d, "--json", "tenant", "link-key",
                                "acme", "key1"])
        data = json.loads(text)
        self.assertEqual(data["api_key_ids"], ["key1"])   # not duplicated

    def test_unlink_key_removes_association(self):
        d = tempfile.mkdtemp()
        self._cli(["--state-dir", d, "tenant", "create", "acme"])
        self._cli(["--state-dir", d, "tenant", "link-key", "acme", "key1"])
        self._cli(["--state-dir", d, "tenant", "unlink-key", "acme", "key1"])
        from aictl.core.tenant import find_tenant_by_key_id
        self.assertIsNone(find_tenant_by_key_id(Path(d), "key1"))

    def test_link_key_unknown_tenant_fails(self):
        d = tempfile.mkdtemp()
        code, text = self._cli(["--state-dir", d, "tenant", "link-key", "ghost", "key1"])
        self.assertEqual(code, 1)
        self.assertIn("not found", text.lower())


# ── proxy enforcement ────────────────────────────────────────────────────────

class _FakeHeaders(dict):
    pass


class _ProxyStub:
    """Binds the real ProxyHandler methods to a lightweight, testable stub."""

    from aictl.daemon.proxy import ProxyHandler as _H
    _check_auth = _H._check_auth
    _meter_tokens = _H._meter_tokens
    _error = _H._error
    _json = _H._json

    def __init__(self, state_dir, bearer=""):
        self.store = type("S", (), {"dir": Path(state_dir)})()
        self.headers = _FakeHeaders()
        if bearer:
            self.headers["Authorization"] = f"Bearer {bearer}"
        self.wfile = io.BytesIO()

    def send_response(self, *a, **k):
        pass

    def send_header(self, *a, **k):
        pass

    def end_headers(self):
        pass


def _make_key(state_dir, name="k1", rpm=100):
    from aictl.core.apikeys import KeyManager
    mgr = KeyManager(Path(state_dir))
    raw, key = mgr.generate_key(name, rate_limit_rpm=rpm)
    return raw, key


class TestProxyTenantEnforcement(unittest.TestCase):
    def test_unlinked_key_bypasses_tenant_check(self):
        # No tenant link at all -> behaves exactly as before this pass.
        d = tempfile.mkdtemp()
        raw, key = _make_key(d)
        stub = _ProxyStub(d, bearer=raw)
        self.assertTrue(stub._check_auth())

    def test_linked_key_under_limit_passes(self):
        d = tempfile.mkdtemp()
        raw, key = _make_key(d)
        from aictl.cmd import tenant as tenant_cmd
        import argparse as _ap
        p = _ap.ArgumentParser(prog="aictl")
        p.add_argument("--state-dir", default=None)
        sub = p.add_subparsers()
        tenant_cmd.register(sub)
        ns = p.parse_args(["--state-dir", d, "tenant", "create", "acme",
                          "--class", "standard"])
        ns.func(ns)
        ns2 = p.parse_args(["--state-dir", d, "tenant", "link-key", "acme", key.key_id])
        ns2.func(ns2)

        stub = _ProxyStub(d, bearer=raw)
        self.assertTrue(stub._check_auth())

    def test_linked_tenant_over_rpm_limit_rejected(self):
        d = tempfile.mkdtemp()
        raw, key = _make_key(d, rpm=10_000)   # per-key limit high, won't trip
        from aictl.cmd import tenant as tenant_cmd
        import argparse as _ap
        p = _ap.ArgumentParser(prog="aictl")
        p.add_argument("--state-dir", default=None)
        sub = p.add_subparsers()
        tenant_cmd.register(sub)
        for argv in (["tenant", "create", "acme", "--class", "dev"],
                    ["tenant", "link-key", "acme", key.key_id]):
            ns = p.parse_args(["--state-dir", d] + argv)
            ns.func(ns)

        from aictl.core.tenant import get_tenant_class
        tc = get_tenant_class("dev")

        # Exhaust the tenant class's request budget directly on the shared
        # limiter (this is exactly what real traffic would do over time).
        from aictl.core.tenant import get_rate_limiter
        limiter = get_rate_limiter()
        for _ in range(tc.max_requests_per_min):
            limiter.record("acme", 0)

        stub = _ProxyStub(d, bearer=raw)
        self.assertFalse(stub._check_auth())

    def test_meter_tokens_records_into_tenant_window(self):
        d = tempfile.mkdtemp()
        raw, key = _make_key(d)
        from aictl.cmd import tenant as tenant_cmd
        import argparse as _ap
        p = _ap.ArgumentParser(prog="aictl")
        p.add_argument("--state-dir", default=None)
        sub = p.add_subparsers()
        tenant_cmd.register(sub)
        for argv in (["tenant", "create", "acme"],
                    ["tenant", "link-key", "acme", key.key_id]):
            ns = p.parse_args(["--state-dir", d] + argv)
            ns.func(ns)

        from aictl.core.tenant import get_rate_limiter
        limiter = get_rate_limiter()
        limiter._windows.pop("acme", None)   # isolate from other tests' state

        stub = _ProxyStub(d, bearer=raw)
        body = {"model": "llama3"}
        response = json.dumps({"usage": {"prompt_tokens": 7, "completion_tokens": 3}}).encode()
        stub._meter_tokens(body, response)

        window = limiter._windows["acme"]
        self.assertEqual(len(window), 1)
        self.assertEqual(window[0][1], 10)   # 7 + 3 tokens recorded once


if __name__ == "__main__":
    unittest.main()

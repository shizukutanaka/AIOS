"""Pass 166 (audit P1+P2+M2): proxy model-trust gate — enforce signed models.

FEATURE_GAP_AUDIT.md's two HIGH-severity paper-only items:
  P1 `trust_policy: enforce` never blocked a model load — only `security scan`
     reported when it was `disabled`; nothing at request time ever refused an
     unsigned model.
  P2 tenant `require_signed_models` (True for the "regulated" class) had 0
     runtime consumers — a regulated tenant could serve unsigned models freely.
Both were inert because of M2: the proxy had no model-level trust interception
point at all.

This adds that point. `ProxyHandler._model_trust_ok(model)` is called in
`_proxy_completion` BEFORE routing; strictest-wins resolution:
  - tenant.require_signed_models -> STRICT (block unsigned), overrides global
  - global trust_policy == 'enforce' -> STRICT
  - global trust_policy == 'disabled' -> allow, no check
  - 'warn' (default) -> allow, audit an unsigned-served warning
A signed model (registry `signed=1`) always passes; an unknown model is treated
as unsigned. Enforcement is fully opt-in: default config is 'warn' with no tenant
requiring signing, so out-of-the-box nothing is blocked.
"""

from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from pathlib import Path


def _store_with_models(d):
    from aictl.core.state import StateStore
    store = StateStore(Path(d))
    store.register_model("sig", "signed-model", "sha256:a", signed=True, signer="ci")
    store.register_model("uns", "unsigned-model", "sha256:b", signed=False)
    return store


def _set_policy(d, policy):
    from aictl.core.config import load_config, save_config
    cfg = load_config(Path(d))
    cfg.trust_policy = policy
    save_config(cfg, Path(d))


def _link_regulated(d, key_id):
    from aictl.cmd import tenant as tc
    p = argparse.ArgumentParser()
    p.add_argument("--state-dir")
    sub = p.add_subparsers()
    tc.register(sub)
    for a in (["tenant", "create", "regco", "--class", "regulated"],
              ["tenant", "link-key", "regco", key_id]):
        ns = p.parse_args(["--state-dir", d] + a)
        ns.func(ns)


class _Stub:
    from aictl.daemon.proxy import ProxyHandler as _H
    _model_trust_ok = _H._model_trust_ok
    _model_is_signed = _H._model_is_signed
    _current_tenant = _H._current_tenant
    _audit = _H._audit

    def __init__(self, store, bearer=""):
        self.store = store
        self.headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}


class TestGlobalTrustPolicy(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.store = _store_with_models(self.d)

    def _ok(self, model, policy):
        _set_policy(self.d, policy)
        return _Stub(self.store)._model_trust_ok(model)[0]

    def test_warn_allows_everything(self):
        self.assertTrue(self._ok("unsigned-model", "warn"))
        self.assertTrue(self._ok("ghost-model", "warn"))
        self.assertTrue(self._ok("signed-model", "warn"))

    def test_enforce_blocks_unsigned_and_unknown(self):
        self.assertFalse(self._ok("unsigned-model", "enforce"))
        self.assertFalse(self._ok("ghost-model", "enforce"))

    def test_enforce_allows_signed(self):
        self.assertTrue(self._ok("signed-model", "enforce"))

    def test_disabled_allows_everything(self):
        self.assertTrue(self._ok("unsigned-model", "disabled"))
        self.assertTrue(self._ok("ghost-model", "disabled"))

    def test_enforce_block_reason_is_actionable(self):
        _set_policy(self.d, "enforce")
        allowed, reason = _Stub(self.store)._model_trust_ok("unsigned-model")
        self.assertFalse(allowed)
        self.assertIn("aictl model verify", reason)


class TestTenantRequireSignedOverride(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.store = _store_with_models(self.d)
        from aictl.core.apikeys import KeyManager
        self.raw, self.key = KeyManager(Path(self.d)).generate_key("k1")

    def test_regulated_tenant_blocks_unsigned_even_when_global_disabled(self):
        _set_policy(self.d, "disabled")       # loosest global
        _link_regulated(self.d, self.key.key_id)
        allowed, reason = _Stub(self.store, bearer=self.raw)._model_trust_ok("unsigned-model")
        self.assertFalse(allowed)             # strictest (tenant) wins
        self.assertIn("regco", reason)

    def test_regulated_tenant_allows_signed(self):
        _set_policy(self.d, "disabled")
        _link_regulated(self.d, self.key.key_id)
        allowed, _ = _Stub(self.store, bearer=self.raw)._model_trust_ok("signed-model")
        self.assertTrue(allowed)

    def test_unlinked_key_not_subject_to_tenant_rule(self):
        _set_policy(self.d, "warn")
        # key exists but linked to no tenant
        allowed, _ = _Stub(self.store, bearer=self.raw)._model_trust_ok("unsigned-model")
        self.assertTrue(allowed)


class TestBlockWritesAudit(unittest.TestCase):
    def test_enforce_block_audited(self):
        d = tempfile.mkdtemp()
        store = _store_with_models(d)
        _set_policy(d, "enforce")
        _Stub(store)._model_trust_ok("unsigned-model")
        from aictl.core.audit import AuditLog
        events = [e.event for e in AuditLog(Path(d)).read(n=10)]
        self.assertIn("proxy.unsigned_model_blocked", events)

    def test_warn_serve_audited_as_warning(self):
        d = tempfile.mkdtemp()
        store = _store_with_models(d)
        _set_policy(d, "warn")
        _Stub(store)._model_trust_ok("unsigned-model")
        from aictl.core.audit import AuditLog
        entries = AuditLog(Path(d)).read(n=10)
        warns = [e for e in entries if e.event == "proxy.unsigned_model_served"]
        self.assertEqual(len(warns), 1)
        self.assertEqual(warns[0].outcome, "warning")


class TestModelIsSigned(unittest.TestCase):
    def test_signed_model_recognized(self):
        d = tempfile.mkdtemp()
        store = _store_with_models(d)
        self.assertTrue(_Stub(store)._model_is_signed("signed-model"))

    def test_unsigned_and_unknown_not_signed(self):
        d = tempfile.mkdtemp()
        store = _store_with_models(d)
        stub = _Stub(store)
        self.assertFalse(stub._model_is_signed("unsigned-model"))
        self.assertFalse(stub._model_is_signed("ghost-model"))
        self.assertFalse(stub._model_is_signed(""))


if __name__ == "__main__":
    unittest.main()

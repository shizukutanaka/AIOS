"""Pass 172: close the /v1/embeddings bypass in the model-trust gate.

Pass 166 added `_model_trust_ok` and wired it into `_proxy_completion`, but
`_proxy_embedding` still routed straight to the upstream engine with no trust
check — so with `trust_policy=enforce` (or a regulated tenant with
`require_signed_models`), an unsigned/unknown model was blocked on
/v1/chat/completions yet fully reachable via /v1/embeddings. Embeddings
requests carry the same document content the policy is trying to keep off
untrusted models, so this was a real policy bypass, not a cosmetic gap.

Fix: `_proxy_embedding` now calls the same `_model_trust_ok(model)` gate
before routing, rejecting with the same 403 + actionable reason. Behavior
under the default config ('warn', no regulated tenant) is unchanged.

Includes a source-level pin that BOTH proxy paths call the gate before
`router.route`, so a future refactor can't silently reopen either bypass.
"""

from __future__ import annotations

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


class _EmbeddingStub:
    """Minimal harness around _proxy_embedding: real gate + real routing
    stubbed out at the router boundary. Mirrors test_new_features_166's
    _Stub, extended with the request-path plumbing _proxy_embedding needs."""

    from aictl.daemon.proxy import ProxyHandler as _H
    _proxy_embedding = _H._proxy_embedding
    _model_trust_ok = _H._model_trust_ok
    _model_is_signed = _H._model_is_signed
    _current_tenant = _H._current_tenant
    _audit = _H._audit

    def __init__(self, store, body):
        self.store = store
        self.headers = {}
        self._body = body
        self.errors = []          # (status, message) from _error
        self.routed = False       # did the request reach the router?

    def _read_body(self):
        return self._body

    def _error(self, status, message, extra=None):
        self.errors.append((status, message))

    def _get_router(self):
        stub = self

        class _Router:
            def route(self, req):
                stub.routed = True

                class _D:
                    endpoint = ""          # "no engine" — stops after routing
                    reason_codes = []
                return _D()
        return _Router()


class TestEmbeddingTrustGate(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.store = _store_with_models(self.d)

    def _run(self, model, policy):
        _set_policy(self.d, policy)
        stub = _EmbeddingStub(self.store, {"model": model, "input": "secret doc"})
        stub._proxy_embedding()
        return stub

    def test_enforce_blocks_unsigned_model_on_embeddings(self):
        # THE bypass: before this pass, this request routed straight through.
        stub = self._run("unsigned-model", "enforce")
        self.assertFalse(stub.routed, "unsigned model must be blocked BEFORE routing")
        self.assertTrue(stub.errors)
        status, msg = stub.errors[0]
        self.assertEqual(status, 403)
        self.assertIn("unsigned-model", msg)

    def test_enforce_blocks_unknown_model_on_embeddings(self):
        stub = self._run("never-registered", "enforce")
        self.assertFalse(stub.routed)
        self.assertEqual(stub.errors[0][0], 403)

    def test_enforce_allows_signed_model_through_to_routing(self):
        stub = self._run("signed-model", "enforce")
        self.assertTrue(stub.routed, "signed model must reach the router")
        # Only the router's own "no engine" 503 — no 403 trust rejection.
        self.assertNotIn(403, [s for s, _ in stub.errors])

    def test_default_warn_policy_is_unchanged(self):
        stub = self._run("unsigned-model", "warn")
        self.assertTrue(stub.routed)
        self.assertNotIn(403, [s for s, _ in stub.errors])

    def test_disabled_policy_skips_the_gate(self):
        stub = self._run("unsigned-model", "disabled")
        self.assertTrue(stub.routed)
        self.assertNotIn(403, [s for s, _ in stub.errors])


class TestBothProxyPathsArePinned(unittest.TestCase):
    """Source-level pin: _proxy_completion AND _proxy_embedding must both call
    _model_trust_ok before router.route — a refactor dropping either silently
    reopens an unsigned-model bypass."""

    def _src(self, name):
        import inspect
        from aictl.daemon.proxy import ProxyHandler
        return inspect.getsource(getattr(ProxyHandler, name))

    def test_completion_gates_before_routing(self):
        src = self._src("_proxy_completion")
        self.assertIn("_model_trust_ok", src)
        self.assertLess(src.index("_model_trust_ok"), src.index("router.route"))

    def test_embedding_gates_before_routing(self):
        src = self._src("_proxy_embedding")
        self.assertIn("_model_trust_ok", src)
        self.assertLess(src.index("_model_trust_ok"), src.index("router.route"))


if __name__ == "__main__":
    unittest.main()

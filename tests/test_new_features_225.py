"""Pass 225: the fair-share gate had a hole, an unkept promise, and no real test.

Backlog item 改善案 #3 said "promote the fair-share advisory to real control".
Re-questioning it against the code found that had already shipped (Pass 198):
`should_admit()` is a genuine decision function and `proxy._check_fair_share()`
returns a real 503, opt-in, default off, failing open. `IMPROVEMENTS.md:852`
records it; `REVIEW_v1.7.0.md` did not, and would have sent a future session to
rebuild it. Fifth stale backlog claim this session.

What was actually wrong sat underneath:

  * **Embeddings bypassed the gate.** `_proxy_embedding` ran the trust and
    guard checks and then skipped fair-share entirely, so an entity deferred on
    `/v1/chat/completions` could keep consuming the same GPU through
    `/v1/embeddings` — tokens that `_meter_tokens` counts identically. A hole
    in an opt-in control, not a design choice.
  * **The 503 promised a header it never sent.** The comment beside it read
    "503 with Retry-After"; `_error()` set no headers at all. A 503 without
    `Retry-After` invites an immediate retry from precisely the client just
    asked to yield.
  * **Nothing tested the rejection.** The 29 tests covering the decision logic
    were good, but the integration was two `inspect.getsource()` substring
    checks that located `"fair_ok, fair_reason"` and asserted `"503"` appeared
    within 220 characters of it. Both would have passed with the gate
    unreachable behind an early return, and neither would notice the embedding
    path missing it. That is the eighth substring-instead-of-behaviour check
    this session; the tests below drive real requests and read real responses.

`RouteRequest.tenant` was also deleted: declared at `router.py:35`, read by
nothing, in a module whose own routing has no tenant concept — a field that
looked like the per-tenant support the backlog was asking for and was not.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from aictl.core.constants import FAIR_SHARE_RETRY_AFTER_SECONDS
from tests.support import IsolatedStateTestCase


class _Recorder:
    """Captures what the handler wrote, without a socket."""

    def __init__(self):
        self.status = None
        self.headers = {}
        self.body = b""

    def send_response(self, status, *a):
        self.status = status

    def send_header(self, name, value):
        self.headers[name] = value

    def end_headers(self):
        pass


class _FakeWfile:
    def __init__(self, sink):
        self.sink = sink

    def write(self, data):
        self.sink.body += data


def _handler(state_dir, policy="enforce"):
    """A ProxyHandler wired to a temp state dir, without binding a socket."""
    from aictl.core.config import load_config, save_config
    from aictl.core.state import StateStore
    from aictl.daemon.proxy import ProxyHandler

    config = load_config(state_dir)
    config.fair_share_policy = policy
    save_config(config, state_dir)

    handler = ProxyHandler.__new__(ProxyHandler)
    handler.store = StateStore(state_dir)
    recorder = _Recorder()
    handler.send_response = recorder.send_response
    handler.send_header = recorder.send_header
    handler.end_headers = recorder.end_headers
    handler.wfile = _FakeWfile(recorder)
    handler.headers = {}
    return handler, recorder


class _FairShareCase(IsolatedStateTestCase):
    """Two entities with a large service skew, so the hog is deferred."""

    def setUp(self):
        super().setUp()
        from aictl.core.metering import TokenMeter

        meter = TokenMeter(self.state_dir)
        meter.record("hog", "m", prompt_tokens=1_000_000,
                     completion_tokens=1_000_000)
        meter.record("quiet", "m", prompt_tokens=10, completion_tokens=10)

    def _decision(self, entity="hog", policy="enforce"):
        handler, recorder = _handler(self.state_dir, policy)
        ok, reason = handler._check_fair_share(entity)
        return ok, reason, handler, recorder


class TestTheGateActuallyDefers(_FairShareCase):
    def test_a_hog_is_deferred_under_enforce(self):
        ok, reason, _, _ = self._decision()
        self.assertFalse(ok)
        self.assertIn("fair-share", reason.lower())

    def test_the_quiet_entity_is_admitted(self):
        ok, _, _, _ = self._decision(entity="quiet")
        self.assertTrue(ok)

    def test_warn_policy_admits_but_still_reports(self):
        ok, _, _, _ = self._decision(policy="warn")
        self.assertTrue(ok, "warn must not deny service")

    def test_off_is_the_default_and_admits_everything(self):
        from aictl.core.config import load_config

        self.assertEqual(getattr(load_config(self.state_dir),
                                 "fair_share_policy", "off"), "off")
        ok, _, _, _ = self._decision(policy="off")
        self.assertTrue(ok)

    def test_an_empty_entity_id_fails_open(self):
        ok, _, _, _ = self._decision(entity="")
        self.assertTrue(ok)

    def test_unreadable_usage_fails_open(self):
        # A fairness mechanism that denies service because it could not read
        # usage has traded a fairness problem for an availability problem.
        handler, _ = _handler(self.state_dir)
        with patch("aictl.core.metering.TokenMeter.list_usage",
                   side_effect=OSError("disk gone")):
            ok, _ = handler._check_fair_share("hog")
        self.assertTrue(ok)


class TestTheDeferralIsRetryable(_FairShareCase):
    """The header the code promised for two passes and never sent."""

    def _error_response(self):
        handler, recorder = _handler(self.state_dir)
        from aictl.daemon.proxy import _retry_after

        handler._error(503, "Deferred by fair-share policy: test",
                       headers=_retry_after())
        return recorder

    def test_status_is_503_not_403(self):
        # Being deferred is transient; 403 would imply a permission failure.
        self.assertEqual(self._error_response().status, 503)

    def test_retry_after_header_is_actually_sent(self):
        self.assertIn("Retry-After", self._error_response().headers)

    def test_retry_after_comes_from_the_constant(self):
        self.assertEqual(self._error_response().headers["Retry-After"],
                         str(FAIR_SHARE_RETRY_AFTER_SECONDS))

    def test_the_body_is_still_json(self):
        payload = json.loads(self._error_response().body)
        self.assertIn("error", payload)

    def test_ordinary_errors_carry_no_retry_after(self):
        # The header must be specific to deferral, not blanket-applied.
        handler, recorder = _handler(self.state_dir)
        handler._error(400, "bad request")
        self.assertNotIn("Retry-After", recorder.headers)


class TestBothProxiedPathsAreGated(unittest.TestCase):
    """The hole: embeddings ran trust and guard, then skipped fair-share."""

    def _path_source(self, func_name: str) -> str:
        import inspect

        from aictl.daemon.proxy import ProxyHandler

        return inspect.getsource(getattr(ProxyHandler, func_name))

    def test_completion_path_calls_the_gate(self):
        self.assertIn("_check_fair_share", self._path_source("_proxy_completion"))

    def test_embedding_path_calls_the_gate(self):
        self.assertIn("_check_fair_share", self._path_source("_proxy_embedding"))

    def test_both_paths_send_retry_after(self):
        for name in ("_proxy_completion", "_proxy_embedding"):
            self.assertIn("_retry_after()", self._path_source(name), name)

    def test_gate_runs_after_trust_and_guard_on_both_paths(self):
        # Scoped to each function rather than the whole module: the previous
        # version searched module-wide, so it could match the ordering in one
        # path while the other had no gate at all — which is exactly what
        # shipped.
        for name in ("_proxy_completion", "_proxy_embedding"):
            source = self._path_source(name)
            trust = source.index("_model_trust_ok")
            guard = source.index("_check_guard")
            fair = source.index("_check_fair_share")
            self.assertLess(trust, guard, name)
            self.assertLess(guard, fair, name)


class TestRouteRequestTenantIsApiSurface(unittest.TestCase):
    """A correction: I called this field dead and was wrong.

    The grep that concluded "declared, never read" covered `aictl/runtime/`
    and `tests/` and not `aictl/daemon/`. `aiosd.py` reads it —
    `RouteRequest(..., tenant=body.get("tenant", ""))` — so it is part of the
    `/v1/broker/route` request contract, and deleting it broke that endpoint
    (caught by `tests/test_phase2.py::test_broker_route`).

    The narrower true observation: the field is accepted and carried, but no
    routing decision consults it. That is a missing feature (tenant-aware
    routing), not dead code, and the remedy is not deletion.
    """

    def test_the_field_exists_because_the_rest_api_accepts_it(self):
        from aictl.runtime.router import RouteRequest

        self.assertIn("tenant", RouteRequest.__dataclass_fields__)

    def test_the_broker_endpoint_still_passes_it(self):
        import inspect

        from aictl.daemon import aiosd

        self.assertIn('tenant=body.get("tenant"', inspect.getsource(aiosd))

    def test_it_defaults_to_empty_so_it_stays_optional(self):
        from aictl.runtime.router import RouteRequest

        self.assertEqual(RouteRequest(model="m").tenant, "")


if __name__ == "__main__":
    unittest.main()

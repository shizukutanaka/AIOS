"""Pass 59 regression tests: OTel empty attribute fix, tenant rate limiter."""

import unittest
import time


class TestOtelEmptyAttributes(unittest.TestCase):
    """build_metric_payload must omit empty node_id/profile attributes."""

    def _payload(self, node_id="", profile=""):
        from aictl.metrics.otel import build_metric_payload
        from aictl.metrics.slo import InferenceMetrics, SystemPressure
        return build_metric_payload(InferenceMetrics(), SystemPressure(),
                                    node_id=node_id, profile=profile)

    def _attr_keys(self, payload):
        return [a["key"] for a in payload["resourceMetrics"][0]["resource"]["attributes"]]

    def test_empty_node_id_omitted(self):
        payload = self._payload(node_id="", profile="")
        keys = self._attr_keys(payload)
        self.assertNotIn("aios.node_id", keys,
                         "empty node_id must not appear as OTel attribute")

    def test_empty_profile_omitted(self):
        payload = self._payload(node_id="", profile="")
        keys = self._attr_keys(payload)
        self.assertNotIn("aios.profile", keys,
                         "empty profile must not appear as OTel attribute")

    def test_non_empty_node_id_included(self):
        payload = self._payload(node_id="node-123", profile="")
        keys = self._attr_keys(payload)
        self.assertIn("aios.node_id", keys)

    def test_non_empty_profile_included(self):
        payload = self._payload(node_id="", profile="gpu-a100")
        keys = self._attr_keys(payload)
        self.assertIn("aios.profile", keys)

    def test_service_name_always_present(self):
        payload = self._payload()
        keys = self._attr_keys(payload)
        self.assertIn("service.name", keys)

    def test_service_version_always_present(self):
        payload = self._payload()
        keys = self._attr_keys(payload)
        self.assertIn("service.version", keys)

    def test_no_empty_string_values(self):
        payload = self._payload(node_id="", profile="")
        for attr in payload["resourceMetrics"][0]["resource"]["attributes"]:
            val = attr["value"].get("stringValue", "NOTSTR")
            self.assertNotEqual(val, "",
                                f"Attribute '{attr['key']}' must not have empty stringValue")


class TestTenantRateLimiter(unittest.TestCase):
    """TenantRateLimiter must enforce per-tenant request and token quotas."""

    def _limiter(self):
        from aictl.core.tenant import TenantRateLimiter
        return TenantRateLimiter()

    def test_first_request_allowed(self):
        lim = self._limiter()
        self.assertTrue(lim.check("t1", "dev"))

    def test_request_under_quota_allowed(self):
        lim = self._limiter()
        lim.record("t1", tokens_used=100)
        self.assertTrue(lim.check("t1", "standard"))

    def test_exceeding_request_limit_blocked(self):
        from aictl.core.tenant import TENANT_CLASSES
        lim = self._limiter()
        tc = TENANT_CLASSES["dev"]  # max_requests_per_min=30
        for _ in range(tc.max_requests_per_min):
            lim.record("t1", tokens_used=0)
        self.assertFalse(lim.check("t1", "dev"),
                         "Request beyond max_requests_per_min must be rejected")

    def test_exceeding_token_limit_blocked(self):
        from aictl.core.tenant import TENANT_CLASSES
        lim = self._limiter()
        tc = TENANT_CLASSES["dev"]  # max_tokens_per_min=50000
        lim.record("t1", tokens_used=tc.max_tokens_per_min)
        self.assertFalse(lim.check("t1", "dev", tokens_requested=1),
                         "Request exceeding token quota must be rejected")

    def test_different_tenants_isolated(self):
        from aictl.core.tenant import TENANT_CLASSES
        lim = self._limiter()
        tc = TENANT_CLASSES["dev"]  # max_requests_per_min=30
        for _ in range(tc.max_requests_per_min):
            lim.record("t1", tokens_used=0)
        # t2 should still be allowed
        self.assertTrue(lim.check("t2", "dev"),
                        "Rate limit on t1 must not affect t2")

    def test_get_rate_limiter_returns_singleton(self):
        from aictl.core.tenant import get_rate_limiter
        lim1 = get_rate_limiter()
        lim2 = get_rate_limiter()
        self.assertIs(lim1, lim2, "get_rate_limiter must return the same singleton")

    def test_token_request_check_before_record(self):
        lim = self._limiter()
        # With no prior usage, token check should pass for reasonable count
        self.assertTrue(lim.check("t1", "standard", tokens_requested=1000))

    def test_rate_limiter_thread_safe(self):
        import threading
        lim = self._limiter()
        errors = []

        def do_work():
            try:
                for _ in range(20):
                    lim.check("t1", "standard")
                    lim.record("t1", tokens_used=10)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=do_work) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(errors, [], f"Thread safety errors: {errors}")


if __name__ == "__main__":
    unittest.main()

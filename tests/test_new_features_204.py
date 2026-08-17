"""Pass 204: flag plaintext HTTP to a non-loopback engine.

Production-readiness checklists for self-hosted inference converge on the same
short list — OpenAI-compatible requests **over HTTPS**, restart-on-failure,
per-key rate limiting, metrics with real alerts, benchmarked performance.
aictl already covers the last four (quotas/apikeys, quadlet units, Prometheus
rules, `bench`). The first was unchecked: `engines conform` accepted
`http://10.0.0.5:8000` silently, with no hint that the Authorization header
and every prompt and completion were crossing the network in cleartext.

Severity is a deliberate fourth value rather than a reuse:
  * not `degraded` — nothing about output quality changes, so calling it that
    would misdescribe the finding;
  * not `required` — the engine works fine, and reporting a working engine as
    broken would be wrong;
  * but it *does* count against `conformant`, because a deployment shipping
    API keys in cleartext is not production-conformant whatever its response
    quality.

Loopback is explicitly exempt. aictl's own defaults are `127.0.0.1`, traffic
there never reaches a wire, and flagging it would make the check fire on
every local deployment — advice that always fires stops being read.
"""

from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout

from aictl.cmd.engines import run_conform
from aictl.runtime.conformance import INSECURE, check_conformance


def _transport(url):
    # Tiny timeout: the transport probe needs no network at all, and the
    # other probes failing fast is exactly what these tests want.
    report = check_conformance(url, timeout=0.05)
    return next(p for p in report.probes if p.name == "transport"), report


class TestLoopbackIsExempt(unittest.TestCase):
    def test_loopback_ip_is_fine(self):
        probe, _ = _transport("http://127.0.0.1:8000")
        self.assertTrue(probe.ok)

    def test_localhost_name_is_fine(self):
        probe, _ = _transport("http://localhost:8000")
        self.assertTrue(probe.ok)

    def test_ipv6_loopback_is_fine(self):
        probe, _ = _transport("http://[::1]:8000")
        self.assertTrue(probe.ok)

    def test_loopback_detail_explains_why_it_is_acceptable(self):
        probe, _ = _transport("http://127.0.0.1:8000")
        self.assertIn("loopback", probe.detail)


class TestRemotePlaintextIsFlagged(unittest.TestCase):
    def test_private_ip_over_http_is_flagged(self):
        probe, _ = _transport("http://10.0.0.5:8000")
        self.assertFalse(probe.ok)
        self.assertEqual(probe.severity, INSECURE)

    def test_public_host_over_http_is_flagged(self):
        probe, _ = _transport("http://engine.example.com:8000")
        self.assertFalse(probe.ok)

    def test_impact_names_the_credential_exposure(self):
        # The actionable part is *what leaks*, not "use TLS".
        probe, _ = _transport("http://10.0.0.5:8000")
        self.assertIn("cleartext", probe.impact)
        self.assertIn("API key", probe.impact)

    def test_flagged_endpoint_is_not_conformant(self):
        _, report = _transport("http://10.0.0.5:8000")
        self.assertFalse(report.conformant)
        self.assertEqual(len(report.failed_insecure), 1)


class TestHttpsPasses(unittest.TestCase):
    def test_https_remote_is_fine(self):
        probe, _ = _transport("https://engine.example.com")
        self.assertTrue(probe.ok)
        self.assertIn("TLS", probe.detail)

    def test_https_does_not_appear_in_failed_insecure(self):
        _, report = _transport("https://engine.example.com")
        self.assertEqual(report.failed_insecure, [])


class TestSeveritySemantics(unittest.TestCase):
    """The fourth severity must not be conflated with the other three."""

    def test_insecure_is_distinct_from_degraded_and_required(self):
        from aictl.runtime.conformance import DEGRADED, OPTIONAL, REQUIRED

        self.assertNotIn(INSECURE, (REQUIRED, DEGRADED, OPTIONAL))

    def test_transport_failure_is_not_counted_as_required(self):
        # A working engine reached over http must not be reported as broken.
        _, report = _transport("http://10.0.0.5:8000")
        self.assertNotIn("transport", [p.name for p in report.failed_required])

    def test_transport_failure_is_not_counted_as_degraded(self):
        _, report = _transport("http://10.0.0.5:8000")
        self.assertNotIn("transport", [p.name for p in report.failed_degraded])


class TestProbeRunsWithoutNetwork(unittest.TestCase):
    def test_transport_probe_is_present_even_when_unreachable(self):
        # It is a property of the URL, not of the server, so it must be
        # answerable when nothing responds.
        probe, report = _transport("http://10.0.0.5:1")
        self.assertFalse(report.reachable)
        self.assertFalse(probe.ok)

    def test_transport_is_the_first_probe(self):
        _, report = _transport("http://10.0.0.5:1")
        self.assertEqual(report.probes[0].name, "transport")

    def test_non_http_scheme_still_short_circuits(self):
        # An unusable URL is a required failure and should not reach here.
        report = check_conformance("file:///etc/passwd")
        self.assertEqual(report.probes[0].name, "endpoint")


class TestCliRendering(unittest.TestCase):
    def _run(self, url, use_json=False):
        namespace = argparse.Namespace(url=url, model="", strict=False,
                                       json=use_json, timeout=0.05)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = run_conform(namespace)
        self.assertEqual(code, 0)
        output = buffer.getvalue()
        return json.loads(output) if use_json else output

    def test_text_output_explains_the_exposure(self):
        output = self._run("http://10.0.0.5:8000")
        self.assertIn("cleartext", output)

    def test_label_says_insecure_not_unavailable(self):
        # "transport unavailable" would be wrong — the transport is present,
        # it is just exposing traffic.
        output = self._run("http://10.0.0.5:8000")
        self.assertIn("transport insecure", output)
        self.assertNotIn("transport unavailable", output)

    def test_loopback_produces_no_exposure_text(self):
        output = self._run("http://127.0.0.1:1")
        self.assertNotIn("cleartext", output)

    def test_json_carries_the_transport_probe(self):
        payload = self._run("http://10.0.0.5:1", use_json=True)
        names = [p["name"] for p in payload[0]["probes"]]
        self.assertIn("transport", names)

    def test_strict_exits_nonzero_for_plaintext_remote(self):
        namespace = argparse.Namespace(url="http://10.0.0.5:1", model="",
                                       strict=True, json=True, timeout=0.05)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = run_conform(namespace)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()

"""Pass 226: fairness measured all-time, so last month throttled you forever.

改善案 #6, the last open item that was blocked by nothing but effort. Both
fairness consumers — the live admission gate and the `tco fairshare` report —
read `TokenBucket.total_tokens`, which is cumulative since the entity first
appeared. A tenant that was heavy last month kept yielding indefinitely, long
after it stopped contending for anything.

The reader half already existed and nothing called it. `TokenMeter.window_usage()`
was fully built — tail-read, byte and event caps, and a `complete` flag — with
its own docstring specifying the contract for throttling callers. Sixth time
this session the backlog described as "to do" something already partly built.
The delta was the config and the two call sites.

Two deliberate asymmetries:

  * **The gate falls back to cumulative on an incomplete window; the report does
    not.** If a cap is hit before the window is covered, the measurement is
    partial. Being *told* a number is partial is fine, so the report shows it
    with the caveat. Being *throttled* on one is not, so the gate declines to
    use it — deferring a tenant on service you failed to measure is worse than
    not deferring.
  * **The gate defaults to a window; the report defaults to cumulative.**
    Admission is about who is contending now. A report is a question the reader
    asked, and changing what `aictl tco fairshare` means without being asked
    would be the silent behaviour change this session keeps finding.

`WindowBucket` claimed to be "shaped like TokenBucket on purpose" and was — for
the scheduler, which reads two fields. `compute_fairness` reads a third
(`entity_type`), so the report crashed on the first real substitution. Shaped
like it for one consumer is not shaped like it.
"""

from __future__ import annotations

import time
import unittest

from aictl.core.constants import FAIR_SHARE_WINDOW_SECONDS
from tests.support import IsolatedStateTestCase


class _MeteredCase(IsolatedStateTestCase):
    """A hog that stopped an hour ago, and a tenant busy right now."""

    OLD = 7200      # seconds ago — outside a 1h window
    NEW = 60

    def setUp(self):
        super().setUp()
        from aictl.core.metering import TokenMeter

        self.meter = TokenMeter(self.state_dir)
        self.meter.record("past-hog", "m", prompt_tokens=1_000_000,
                          completion_tokens=1_000_000)
        self.meter.record("now-busy", "m", prompt_tokens=1_000,
                          completion_tokens=1_000)
        self._age_log()

    def _age_log(self):
        """Backdate the hog's event so only it falls outside the window."""
        import json

        path = self.state_dir / "metering_log.jsonl"
        now = time.time()
        rewritten = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            event["timestamp"] = now - (self.OLD if event["entity_id"] == "past-hog"
                                        else self.NEW)
            rewritten.append(json.dumps(event))
        path.write_text("\n".join(rewritten) + "\n")


class TestTheWindowChangesWhatIsMeasured(_MeteredCase):
    def test_cumulative_still_sees_the_old_hog(self):
        totals = {b.entity_id: b.total_tokens for b in self.meter.list_usage()}
        self.assertEqual(totals["past-hog"], 2_000_000)

    def test_a_one_hour_window_does_not(self):
        # The whole point: usage from two hours ago is not current contention.
        usage = self.meter.window_usage(3600)
        self.assertTrue(usage.complete)
        self.assertNotIn("past-hog", usage.buckets)
        self.assertIn("now-busy", usage.buckets)

    def test_a_wide_window_sees_both(self):
        usage = self.meter.window_usage(86400)
        self.assertEqual(set(usage.buckets), {"past-hog", "now-busy"})

    def test_window_preserves_the_prompt_completion_split(self):
        # The reason the window is built from the log rather than TokenBucket's
        # calendar counters, which carry no split.
        bucket = self.meter.window_usage(3600).buckets["now-busy"]
        self.assertEqual((bucket.prompt_tokens, bucket.completion_tokens),
                         (1_000, 1_000))


class TestTheGateStopsPunishingOldUsage(_MeteredCase):
    """The behavioural change, driven through the real gate."""

    def _admits(self, entity, window):
        from aictl.core.config import load_config, save_config
        from aictl.core.state import StateStore
        from aictl.daemon.proxy import ProxyHandler

        config = load_config(self.state_dir)
        config.fair_share_policy = "enforce"
        config.fair_share_window_seconds = window
        save_config(config, self.state_dir)

        handler = ProxyHandler.__new__(ProxyHandler)
        handler.store = StateStore(self.state_dir)
        ok, _ = handler._check_fair_share(entity)
        return ok

    def test_cumulative_defers_the_entity_that_is_no_longer_busy(self):
        # The old behaviour, kept reachable via window=0.
        self.assertFalse(self._admits("past-hog", window=0))

    def test_a_window_admits_it_again(self):
        # Same data, same gate — only the measurement window differs.
        self.assertTrue(self._admits("past-hog", window=3600))

    def test_the_currently_busy_entity_is_admitted_either_way(self):
        # It is the least-served in both measures, so nothing defers it.
        self.assertTrue(self._admits("now-busy", window=0))
        self.assertTrue(self._admits("now-busy", window=3600))

    def test_the_default_window_is_not_cumulative(self):
        from aictl.core.config import Config

        self.assertGreater(Config().fair_share_window_seconds, 0)
        self.assertEqual(Config().fair_share_window_seconds,
                         float(FAIR_SHARE_WINDOW_SECONDS))


class TestIncompleteWindowsDoNotThrottle(_MeteredCase):
    """Deferring on service you failed to measure is worse than not deferring."""

    def test_an_incomplete_window_is_reported_as_such(self):
        # A cap hit before the window is covered.
        usage = self.meter.window_usage(86400, max_events=1)
        self.assertFalse(usage.complete)

    def test_the_gate_falls_back_to_cumulative_when_incomplete(self):
        from unittest.mock import patch

        from aictl.core.metering import TokenMeter, WindowedUsage

        # An incomplete window that would otherwise have exonerated the hog.
        incomplete = WindowedUsage({}, 3600.0, False, 0)
        with patch.object(TokenMeter, "window_usage", return_value=incomplete):
            deferred = not self._admits_with_patch("past-hog")
        self.assertTrue(deferred,
                        "an unmeasurable window must not silently exonerate")

    def _admits_with_patch(self, entity):
        from aictl.core.config import load_config, save_config
        from aictl.core.state import StateStore
        from aictl.daemon.proxy import ProxyHandler

        config = load_config(self.state_dir)
        config.fair_share_policy = "enforce"
        config.fair_share_window_seconds = 3600.0
        save_config(config, self.state_dir)
        handler = ProxyHandler.__new__(ProxyHandler)
        handler.store = StateStore(self.state_dir)
        ok, _ = handler._check_fair_share(entity)
        return ok


class TestTheReportOptsIn(_MeteredCase):
    """A report is a question the reader asked; its meaning must not shift."""

    def _fairshare(self, window=0.0):
        import argparse
        import io
        import json as _json
        from contextlib import redirect_stdout

        from aictl.cmd.tco import run_fairshare

        namespace = argparse.Namespace(json=True, window=window)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            run_fairshare(namespace)
        return _json.loads(buffer.getvalue())

    def test_default_is_still_cumulative(self):
        payload = self._fairshare()
        self.assertNotIn("window", payload)
        ids = {e["entity_id"] for e in payload["entities"]}
        self.assertIn("past-hog", ids)

    def test_window_flag_changes_the_measure(self):
        payload = self._fairshare(window=3600.0)
        ids = {e["entity_id"] for e in payload["entities"]}
        self.assertNotIn("past-hog", ids)

    def test_window_metadata_is_reported(self):
        payload = self._fairshare(window=3600.0)
        self.assertTrue(payload["window"]["complete"])
        self.assertEqual(payload["window"]["window_seconds"], 3600.0)

    def test_the_report_shows_an_incomplete_window_rather_than_hiding_it(self):
        # Unlike the gate: being told a number is partial is fine.
        from unittest.mock import patch

        from aictl.core.metering import TokenMeter, WindowedUsage

        with patch.object(TokenMeter, "window_usage",
                          return_value=WindowedUsage({}, 3600.0, False, 5)):
            payload = self._fairshare(window=3600.0)
        self.assertFalse(payload["window"]["complete"])


class TestConfigSetValidates(IsolatedStateTestCase):
    """A typo in an enforcement policy silently downgraded it to a warning.

    `aictl config set` type-coerced and saved, and never called
    `_validate_config` — which already knew every rule, and ran only on
    `validate` and `import`. So `fair_share_policy bogus` stored fine, and the
    gate reads it as "not off, not enforce", i.e. warn. Someone who typed it
    would believe they were enforcing.

    Pre-existing; found while testing this pass's own new field, which
    inherited the same hole.
    """

    def _set(self, key, value):
        import argparse
        import io
        from contextlib import redirect_stdout, redirect_stderr

        from aictl.cmd.config import run_set

        namespace = argparse.Namespace(state_dir=str(self.state_dir),
                                       key=key, value=str(value), json=False)
        out, errbuf = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(errbuf):
            code = run_set(namespace)
        return code, out.getvalue() + errbuf.getvalue()

    def test_an_invalid_policy_is_refused(self):
        code, output = self._set("fair_share_policy", "bogus")
        self.assertEqual(code, 1)
        self.assertIn("Refusing", output)

    def test_the_invalid_value_is_not_persisted(self):
        from aictl.core.config import load_config

        self._set("fair_share_policy", "bogus")
        self.assertEqual(load_config(self.state_dir).fair_share_policy, "off")

    def test_an_out_of_range_ratio_is_refused(self):
        self.assertEqual(self._set("fair_share_yield_ratio", "0.5")[0], 1)

    def test_a_negative_window_is_refused(self):
        self.assertEqual(self._set("fair_share_window_seconds", "-5")[0], 1)

    def test_zero_window_is_allowed_because_it_is_documented(self):
        # RELEASE.md tells upgraders to set exactly this to keep the old
        # behaviour; rejecting it alongside negatives would break that advice.
        code, _ = self._set("fair_share_window_seconds", "0")
        self.assertEqual(code, 0)

    def test_valid_values_still_save(self):
        from aictl.core.config import load_config

        self.assertEqual(self._set("fair_share_policy", "enforce")[0], 0)
        self.assertEqual(load_config(self.state_dir).fair_share_policy, "enforce")

    def test_an_unrelated_key_is_not_blocked_by_another_fields_problem(self):
        # Validating the whole config on every set would let a pre-existing
        # invalid value block the edit that fixes it.
        from aictl.core.config import load_config, save_config

        config = load_config(self.state_dir)
        config.fair_share_yield_ratio = 0.5      # invalid, written directly
        save_config(config, self.state_dir)
        self.assertEqual(self._set("fair_share_policy", "warn")[0], 0)


class TestWindowBucketFitsBothConsumers(unittest.TestCase):
    def test_it_carries_what_compute_fairness_reads(self):
        # It shipped without entity_type, so the report crashed on the first
        # real substitution while the scheduler was fine.
        from aictl.core.metering import WindowBucket

        bucket = WindowBucket("e", 1, 2)
        for attr in ("entity_id", "entity_type", "total_tokens"):
            self.assertTrue(hasattr(bucket, attr), attr)

    def test_it_carries_what_the_scheduler_reads(self):
        from aictl.core.fair_scheduler import weighted_service
        from aictl.core.metering import WindowBucket

        self.assertEqual(weighted_service(WindowBucket("e", 10, 10),
                                          output_weight=2.0), 30.0)

    def test_compute_fairness_accepts_window_buckets(self):
        from aictl.core.fairness import compute_fairness
        from aictl.core.metering import WindowBucket

        report = compute_fairness([WindowBucket("a", 10, 10),
                                   WindowBucket("b", 10, 10)])
        self.assertEqual(report.entity_count, 2)


if __name__ == "__main__":
    unittest.main()

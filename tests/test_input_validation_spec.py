"""Conformance tests for docs/INPUT_VALIDATION_SPEC.md (V-invariants).

These lock in the input-validation contracts established across the robustness
audit so a regression fails `aictl gate`:

  V1/V3 — count/size flags reject sub-minimum values (and the negative-slice
          trap can never be reached)
  V2    — identifier hygiene is symmetric (strip + reject empty, create→query)
  V4    — user input errors surface as "Invalid input", never "report a bug"
  V5    — a rejected --json invocation exits non-zero with no JSON body
"""

from __future__ import annotations

import argparse
import io
import json as _json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch


def _silent(fn, ns):
    """Run a handler, swallowing stdout/stderr; return its exit code."""
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return fn(ns)


def _stdout(fn, ns):
    """Run a handler, return (exit_code, captured_stdout)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fn(ns)
    return rc, buf.getvalue()


class TestV1V3CountFlagsRejectSubMinimum(unittest.TestCase):
    """V1/V3: count/size flags reject -1 and 0 with a non-zero exit."""

    def _cases(self):
        from aictl.cmd.recommend import run as recommend_run
        from aictl.cmd.optimize import run as optimize_run
        from aictl.cmd.route import run_test as route_test
        from aictl.cmd.fit import run as fit_run
        from aictl.cmd.tco import run_summary as tco_summary

        return [
            ("recommend --top",
             recommend_run, lambda v: argparse.Namespace(top=v, use_case="", json=False)),
            ("optimize --top",
             optimize_run, lambda v: argparse.Namespace(top=v, engine="", state_dir=None, json=False)),
            ("route test --n",
             route_test, lambda v: argparse.Namespace(n=v, json=False)),
            ("fit --context",
             fit_run, lambda v: argparse.Namespace(model="llama3:8b", gpu="H100",
                                                   context=v, concurrent=1, use_case="", json=False)),
            ("fit --concurrent",
             fit_run, lambda v: argparse.Namespace(model="llama3:8b", gpu="H100",
                                                   context=8192, concurrent=v, use_case="", json=False)),
            ("tco --period-days",
             tco_summary, lambda v: argparse.Namespace(period_days=v, carbon_intensity=None, json=False)),
        ]

    def test_negative_rejected(self):
        for label, fn, mk in self._cases():
            self.assertEqual(_silent(fn, mk(-1)), 1, f"{label} must reject -1 with exit 1")

    def test_zero_rejected(self):
        for label, fn, mk in self._cases():
            self.assertEqual(_silent(fn, mk(0)), 1, f"{label} must reject 0 with exit 1")


class TestV1V3LibraryGuards(unittest.TestCase):
    """V3: library chokepoints return [] for non-positive counts (no inverted slice)."""

    def test_recommend_lib_guards_max_results(self):
        from aictl.runtime.recommend import recommend
        self.assertEqual(recommend(vram_mb=80000, max_results=-3), [])
        self.assertEqual(recommend(vram_mb=80000, max_results=0), [])

    def test_search_guards_k(self):
        from aictl.core.rag import search, RagStore
        self.assertEqual(search("hello world", RagStore(), k=-3), [])
        self.assertEqual(search("hello world", RagStore(), k=0), [])

    def test_warmup_guards_top_n(self):
        from aictl.runtime.warmup import WarmupManager
        from aictl.core.state import StateStore
        mgr = WarmupManager(StateStore(tempfile.mkdtemp()))
        self.assertEqual(mgr.get_warmup_candidates(top_n=-3), [])
        self.assertEqual(mgr.get_warmup_candidates(top_n=0), [])

    def test_eventbus_recent_guards_n(self):
        from aictl.core.events import EventBus, Event
        bus = EventBus()
        bus.publish(Event(type="x"))
        bus.publish(Event(type="y"))
        self.assertEqual(bus.recent(0), [])       # guard, not empty-history luck
        self.assertEqual(bus.recent(-5), [])


class TestV2IdentifierHygiene(unittest.TestCase):
    """V2: strip + reject empty, and create→query symmetric under padding."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = patch.dict(os.environ, {"AIOS_STATE_DIR": self.tmp})
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_tenant_create_query_symmetric(self):
        from aictl.cmd.tenant import run_create, run_inspect
        a = lambda tid: argparse.Namespace(tenant_id=tid, name="", tenant_class="standard",
                                           json=False, state_dir=self.tmp)
        self.assertEqual(_silent(run_create, a("team ")), 0)
        self.assertEqual(_silent(run_inspect, a("team")), 0)        # padded→trimmed
        self.assertEqual(_silent(run_create, a("   ")), 1)          # empty rejected

    def test_meter_quota_create_query_symmetric(self):
        from aictl.cmd.meter import run_quota, run_usage
        self.assertEqual(_silent(run_quota, argparse.Namespace(
            entity=" eng ", per_day=50, per_month=None, json=False)), 0)
        rc, out = _stdout(run_usage, argparse.Namespace(entity="eng", json=True))
        self.assertEqual(rc, 0)
        self.assertEqual(len(_json.loads(out)), 1)                  # trimmed key found

    def test_meter_quota_empty_and_negative_rejected(self):
        from aictl.cmd.meter import run_quota
        self.assertEqual(_silent(run_quota, argparse.Namespace(
            entity="   ", per_day=1, per_month=None, json=False)), 1)   # empty id
        self.assertEqual(_silent(run_quota, argparse.Namespace(
            entity="eng", per_day=-1, per_month=None, json=False)), 1)  # negative quota


class TestV4ErrorHonesty(unittest.TestCase):
    """V4: user input errors are "Invalid input", never "report a bug"."""

    def test_valueerror_is_invalid_input_not_bug(self):
        from aictl.core.errors import format_for_user
        msg = format_for_user(ValueError("min_replicas (10) must be <= max_replicas (2)"))
        self.assertIn("Invalid input", msg)
        self.assertNotIn("report", msg.lower())

    def test_keyerror_is_invalid_input(self):
        from aictl.core.errors import format_for_user
        msg = format_for_user(KeyError("missing"))
        self.assertIn("Invalid input", msg)

    def test_interval_parsers_survive_empty_string(self):
        # V4: empty unit string must degrade, not raise IndexError.
        from aictl.cmd.warmup import _parse_interval_secs
        self.assertEqual(_parse_interval_secs(""), 3600)


class TestV5JsonRejectionContract(unittest.TestCase):
    """V5: a rejected --json invocation exits non-zero with no JSON body."""

    def test_tco_json_rejection_emits_no_body(self):
        from aictl.cmd.tco import run_summary
        rc, out = _stdout(run_summary, argparse.Namespace(
            period_days=-30, carbon_intensity=None, json=True))
        self.assertEqual(rc, 1)
        self.assertEqual(out.strip(), "")           # no partial JSON on stdout

    def test_meter_quota_json_rejection_is_clean(self):
        # Negative quota under --json: exit 1, and stdout is either empty or a
        # single JSON object carrying an "error" (never a success body).
        from aictl.cmd.meter import run_quota
        with tempfile.TemporaryDirectory() as d, \
                patch.dict(os.environ, {"AIOS_STATE_DIR": d}):
            rc, out = _stdout(run_quota, argparse.Namespace(
                entity="eng", per_day=-1, per_month=None, json=True))
        self.assertEqual(rc, 1)
        if out.strip():
            self.assertIn("error", _json.loads(out))


if __name__ == "__main__":
    unittest.main()

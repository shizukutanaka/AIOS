"""Pass 112 (loop): a "last N" limit of 0 must mean none, not everything.

The slice idiom lst[-n:] is a trap for n<=0: lst[-0:] == lst[:] returns the
WHOLE list, and negative n slices nonsensically. Several "show last N" paths
fed a user-supplied -n / --last straight into this idiom, so `-n 0` returned
all events/records instead of zero:

  - EventBus.recent(n)        -> aictl events list -n 0
  - perf.read_recent(limit)   -> public API used by dashboards
  - aictl alert history -n 0  -> alert_events[-n:]
  - aictl health history/trends -n 0 -> events[-n:]

All now treat n<=0 as an empty result.
"""

from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from aictl.core.events import EventBus, Event


def _bus_with(n_events: int, etype: str = "x") -> EventBus:
    b = EventBus()
    for i in range(n_events):
        b.publish(Event(type=etype, source="s", data={"i": i}))
    return b


class TestEventBusRecentLimit(unittest.TestCase):
    def test_zero_returns_empty_not_all(self):
        b = _bus_with(5)
        self.assertEqual(b.recent(n=0), [])          # was: all 5
        self.assertEqual(len(b.recent(n=2)), 2)

    def test_negative_returns_empty(self):
        b = _bus_with(5)
        self.assertEqual(b.recent(n=-3), [])          # was: 2 (garbage slice)

    def test_zero_with_event_type_filter(self):
        b = _bus_with(5, etype="health.snapshot")
        self.assertEqual(b.recent(n=0, event_type="health.snapshot"), [])


class TestPerfReadRecentLimit(unittest.TestCase):
    def test_zero_limit_returns_empty(self):
        from aictl.core import perf
        # Should short-circuit to [] before touching the file at all.
        self.assertEqual(perf.read_recent(0), [])
        self.assertEqual(perf.read_recent(-5), [])


class TestAlertHistoryLimit(unittest.TestCase):
    def test_alert_history_n_zero_emits_no_events(self):
        from aictl.cmd.alert import run_history
        b = _bus_with(4, etype="slo.violation")
        with patch("aictl.cmd.alert.get_bus", return_value=b):
            buf = io.StringIO()
            with redirect_stdout(buf):
                run_history(argparse.Namespace(last=0, json=True))
            data = json.loads(buf.getvalue())
        self.assertEqual(data, [])  # was: all 4 alert events


class TestHealthHistoryLimit(unittest.TestCase):
    def test_health_history_n_zero_emits_no_events(self):
        from aictl.cmd.health import run_history
        b = _bus_with(4, etype="health.snapshot")
        with patch("aictl.core.events.get_bus", return_value=b):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_history(argparse.Namespace(last=0, json=True))
            out = buf.getvalue()
        # n=0 -> no events -> empty JSON array (not all 4 snapshots).
        self.assertEqual(json.loads(out), [])


if __name__ == "__main__":
    unittest.main()

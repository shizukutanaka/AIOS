"""Shared test support — one canonical way to isolate ambient state.

Extracted because the same ~14-line setUp/tearDown pair had been copy-pasted
into ten places across four test files, and getting it subtly wrong was the
direct cause of two defects in this codebase:

  * the suite writing prefix-reuse data into the developer's real ~/.aios, and
  * tests that passed only in `discover` order because they inherited state
    from whichever file ran first.

Duplication of a tricky pattern is worse than duplication of a simple one: ten
copies means ten chances to forget the `AIOS_STATE_DIR` half, or to restore the
environment in a way that leaks on failure. One implementation, used
everywhere, makes the correct thing the easy thing.

Both environment names are set deliberately. The codebase reads
`AICTL_STATE_DIR` in some modules and `AIOS_STATE_DIR` in others (the RAG store
and semantic cache use the latter); setting only one leaves a module pointed at
the real state directory, which is exactly the bug this exists to prevent.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_STATE_ENV_NAMES = ("AICTL_STATE_DIR", "AIOS_STATE_DIR")


class IsolatedStateTestCase(unittest.TestCase):
    """A TestCase whose ambient state cannot touch the real state directory.

    Subclasses that need their own setUp must call `super().setUp()` first,
    otherwise the isolation is not yet in place when their code runs.

    `self.state_dir` is the temporary directory, for tests that need to look
    at what was written.
    """

    state_dir: Path

    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory(prefix="aictl-test-")
        self.state_dir = Path(self._tmp.name)
        self._saved = {name: os.environ.get(name) for name in _STATE_ENV_NAMES}
        for name in _STATE_ENV_NAMES:
            os.environ[name] = self._tmp.name
        # Registered rather than left to tearDown: cleanup then runs even if a
        # subclass's setUp raises after this point, which a plain tearDown
        # would not.
        self.addCleanup(self._restore_state)

    def _restore_state(self) -> None:
        for name, previous in self._saved.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
        self._tmp.cleanup()


class IsolatedTrackerTestCase(IsolatedStateTestCase):
    """Isolated state *and* a clean process-global prefix-reuse tracker.

    The tracker is a module singleton, so a test that leaves counts in it
    changes what a later test measures — the failure mode that produced an
    order-dependent test earlier in this codebase's history. Both its counters
    and its persistence flag are restored, since the daemon flips the latter.
    """

    def setUp(self) -> None:
        super().setUp()
        from aictl.runtime.prefix_route import get_default_tracker

        tracker = get_default_tracker()
        self._prev_persist = tracker.persistence_enabled()
        tracker.enable_persistence(False)
        tracker.clear()
        self.addCleanup(self._restore_tracker)

    def _restore_tracker(self) -> None:
        from aictl.runtime.prefix_route import get_default_tracker

        tracker = get_default_tracker()
        tracker.clear()
        tracker.enable_persistence(self._prev_persist)

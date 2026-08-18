"""Test package. Importing it makes the suite hermetic.

Running the tests used to write into the developer's real `~/.aios`: 53 of the
280 test files left `models.db`, `rag.db`, `sem_cache.db`, `perf.jsonl`, the
audit log or the daemon logs behind. Two consequences, and the second is worse
than the first.

The obvious one is that running the tests mutated real data — the model
registry, the audit trail, the RAG index.

The subtle one is that the suite was not hermetic. A test that reads the
default state directory could pass because of something a previous run left
there, and fail on a clean machine, or vice versa. That failure mode has
already appeared in this codebase twice: a tracker writing to the real state
directory, and tests that passed only in `discover` order.

Fixing 53 files individually would have been the wrong repair. Every one of
those artifacts is a *state-directory* artifact, so redirecting the state
directory once — here, before any test module has imported anything from
`aictl` — covers all of them and any test written later. `tests/support.py`
remains the right tool for a test that wants its *own* directory to inspect;
this is the floor beneath it.

An already-set value is honoured rather than overwritten, because
`core/partest.py` gives each parallel worker its own directory and must keep
it.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

_ENV_NAMES = ("AIOS_STATE_DIR", "AICTL_STATE_DIR")

if not any(os.environ.get(name) for name in _ENV_NAMES):
    _suite_state_dir = tempfile.mkdtemp(prefix="aictl-suite-")
    for _name in _ENV_NAMES:
        os.environ[_name] = _suite_state_dir
    atexit.register(shutil.rmtree, _suite_state_dir, ignore_errors=True)

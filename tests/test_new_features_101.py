"""Pass 101 (loop): daemon startup is robust to a port conflict.

New lens: operational startup failure. serve() started the SLO Governor thread
BEFORE binding the port, so a port conflict raised an ugly OSError traceback AND
leaked the governor thread. serve() now binds first: a conflict exits cleanly
(SystemExit 1) with an actionable message, and the governor is only started once
the port is owned (no leak).
"""

from __future__ import annotations

import io
import socket
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from pathlib import Path


class TestDaemonPortConflict(unittest.TestCase):

    def _occupied_port(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        return s, s.getsockname()[1]

    def test_port_in_use_exits_cleanly(self):
        from aictl.daemon import aiosd
        s, port = self._occupied_port()
        try:
            buf = io.StringIO()
            with self.assertRaises(SystemExit) as cm, redirect_stderr(buf):
                aiosd.serve(host="127.0.0.1", port=port,
                            state_dir=Path(tempfile.mkdtemp()))
            self.assertEqual(cm.exception.code, 1)
            msg = buf.getvalue().lower()
            self.assertIn("already in use", msg)
            self.assertIn("daemon status", msg)  # actionable next step
        finally:
            s.close()

    def test_no_governor_thread_leak_on_bind_failure(self):
        from aictl.daemon import aiosd
        s, port = self._occupied_port()
        try:
            before = threading.active_count()
            with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                aiosd.serve(host="127.0.0.1", port=port,
                            state_dir=Path(tempfile.mkdtemp()))
            # The governor (a background thread) must not have been started.
            self.assertEqual(threading.active_count(), before)
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()

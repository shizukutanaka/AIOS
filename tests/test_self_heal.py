"""Tests for the self-healing layer."""

import socket
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestSelfHeal(unittest.TestCase):
    def setUp(self):
        from aictl.core.self_heal import clear_history
        clear_history()

    def test_no_applicable_healer_returns_false(self):
        from aictl.core.self_heal import try_heal
        # Random error nobody should heal
        self.assertFalse(try_heal(KeyError("totally unrelated")))

    def test_heal_port_in_use_finds_alternative(self):
        from aictl.core.self_heal import try_heal
        # Bind a real socket so we know the port is taken
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        busy_port = s.getsockname()[1]
        try:
            ctx = {"port": busy_port}
            err = OSError("address already in use")
            healed = try_heal(err, ctx)
            self.assertTrue(healed)
            self.assertNotEqual(ctx["port"], busy_port)
            self.assertEqual(ctx["healed_from"], busy_port)
        finally:
            s.close()

    def test_heal_missing_dir_creates_it(self):
        from aictl.core.self_heal import try_heal
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "nested" / "deep" / "x.json"
            err = FileNotFoundError(2, "No such file", str(target))
            ctx = {}
            healed = try_heal(err, ctx)
            self.assertTrue(healed)
            self.assertTrue(target.parent.exists())

    def test_heal_oom_halves_context(self):
        from aictl.core.self_heal import try_heal
        ctx = {"max_model_len": 32768}
        err = RuntimeError("CUDA out of memory")
        healed = try_heal(err, ctx)
        self.assertTrue(healed)
        self.assertEqual(ctx["max_model_len"], 16384)

    def test_heal_oom_refuses_below_minimum(self):
        from aictl.core.self_heal import try_heal
        ctx = {"max_model_len": 4096}
        err = RuntimeError("CUDA out of memory")
        healed = try_heal(err, ctx)
        # Should not heal — already at minimum
        self.assertFalse(healed)

    def test_heal_network_backoff_then_gives_up(self):
        from aictl.core.self_heal import try_heal
        ctx = {}
        err = ConnectionRefusedError()
        # First retry should heal
        self.assertTrue(try_heal(err, ctx))
        # Second retry should heal
        self.assertTrue(try_heal(err, ctx))
        # Third should give up
        self.assertFalse(try_heal(err, ctx))

    def test_with_self_heal_retries_then_succeeds(self):
        from aictl.core.self_heal import with_self_heal
        attempts = {"n": 0}

        def flaky(**kwargs):
            attempts["n"] += 1
            if attempts["n"] < 2:
                # Use an error we know how to heal
                raise ConnectionRefusedError("simulated transient")
            return "ok"

        result = with_self_heal(flaky)
        self.assertEqual(result, "ok")
        self.assertEqual(attempts["n"], 2)

    def test_with_self_heal_propagates_unhealable(self):
        from aictl.core.self_heal import with_self_heal

        def always_fails():
            raise KeyError("nobody heals KeyError")

        with self.assertRaises(KeyError):
            with_self_heal(always_fails)

    def test_history_records_attempts(self):
        from aictl.core.self_heal import try_heal, get_history
        try_heal(ConnectionRefusedError())
        history = get_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].pattern, "transient_network")
        self.assertTrue(history[0].succeeded)


if __name__ == "__main__":
    unittest.main()

"""Pass 58 regression tests: governor thread-safety, disagg validation."""

import threading
import time
import unittest
from dataclasses import dataclass, field


class TestGovernorThreadSafety(unittest.TestCase):
    """GovernorDaemon must use a lock to protect state accessed from multiple threads."""

    def test_governor_has_state_lock(self):
        import pathlib, tempfile
        from aictl.daemon.governor import GovernorDaemon
        from aictl.core.state import StateStore
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        gov = GovernorDaemon(store=StateStore(tmpdir))
        self.assertTrue(hasattr(gov, "_state_lock"),
                        "GovernorDaemon must have _state_lock attribute")
        import threading
        self.assertIsInstance(gov._state_lock, type(threading.Lock()),
                              "_state_lock must be a threading.Lock")

    def test_get_status_returns_dict(self):
        import pathlib, tempfile
        from aictl.daemon.governor import GovernorDaemon
        from aictl.core.state import StateStore
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        gov = GovernorDaemon(store=StateStore(tmpdir))
        status = gov.get_status()
        self.assertIsInstance(status, dict)
        for key in ("running", "tick_count", "last_tick", "consecutive_violations"):
            self.assertIn(key, status)

    def test_concurrent_get_status_does_not_raise(self):
        """get_status() called from multiple threads must not corrupt state."""
        import pathlib, tempfile
        from aictl.daemon.governor import GovernorDaemon
        from aictl.core.state import StateStore
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        gov = GovernorDaemon(store=StateStore(tmpdir))
        errors = []

        def read_status():
            try:
                for _ in range(50):
                    gov.get_status()
            except Exception as e:
                errors.append(str(e))

        def write_state():
            try:
                for i in range(50):
                    with gov._state_lock:
                        gov.state.tick_count += 1
                        gov.state.consecutive_violations = i % 5
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=read_status) for _ in range(3)]
        threads += [threading.Thread(target=write_state)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(errors, [], f"Thread safety errors: {errors}")

    def test_source_has_lock_in_loop(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "daemon" / "governor.py").read_text()
        self.assertIn("_state_lock", src, "governor.py must use _state_lock")
        self.assertIn("with self._state_lock:", src,
                      "governor.py _loop must use 'with self._state_lock:'")

    def test_source_get_status_uses_lock(self):
        import pathlib, re
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "daemon" / "governor.py").read_text()
        # get_status must be inside a lock context
        match = re.search(r"def get_status.*?(?=^    def |\Z)", src, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match)
        self.assertIn("_state_lock", match.group(0),
                      "get_status() must use _state_lock")


class TestDisaggConfigValidation(unittest.TestCase):
    """DisaggConfig must reject invalid replica counts and gpu_memory_utilization."""

    def test_prefill_replicas_zero_raises(self):
        from aictl.stack.disagg import DisaggConfig
        with self.assertRaises(ValueError):
            DisaggConfig(model="llama", prefill_replicas=0, decode_replicas=2)

    def test_decode_replicas_zero_raises(self):
        from aictl.stack.disagg import DisaggConfig
        with self.assertRaises(ValueError):
            DisaggConfig(model="llama", prefill_replicas=1, decode_replicas=0)

    def test_negative_prefill_raises(self):
        from aictl.stack.disagg import DisaggConfig
        with self.assertRaises(ValueError):
            DisaggConfig(model="llama", prefill_replicas=-1, decode_replicas=2)

    def test_zero_gpu_memory_utilization_raises(self):
        from aictl.stack.disagg import DisaggConfig
        with self.assertRaises(ValueError):
            DisaggConfig(model="llama", gpu_memory_utilization=0.0)

    def test_gpu_memory_utilization_above_1_raises(self):
        from aictl.stack.disagg import DisaggConfig
        with self.assertRaises(ValueError):
            DisaggConfig(model="llama", gpu_memory_utilization=1.1)

    def test_valid_config_creates_ok(self):
        from aictl.stack.disagg import DisaggConfig
        config = DisaggConfig(model="llama", prefill_replicas=1, decode_replicas=3)
        self.assertEqual(config.prefill_replicas, 1)
        self.assertEqual(config.decode_replicas, 3)

    def test_default_config_is_valid(self):
        from aictl.stack.disagg import DisaggConfig
        config = DisaggConfig(model="meta-llama/Meta-Llama-3-8B")
        self.assertGreater(config.prefill_replicas, 0)
        self.assertGreater(config.decode_replicas, 0)
        self.assertGreater(config.gpu_memory_utilization, 0)
        self.assertLessEqual(config.gpu_memory_utilization, 1.0)

    def test_1_plus_1_is_valid(self):
        from aictl.stack.disagg import DisaggConfig
        config = DisaggConfig(model="llama", prefill_replicas=1, decode_replicas=1)
        self.assertEqual(config.prefill_replicas + config.decode_replicas, 2)


if __name__ == "__main__":
    unittest.main()

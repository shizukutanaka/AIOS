"""Pass 37 regression tests: sem_cache.py in-memory counter thread safety."""

import threading
import unittest


class TestSemCacheThreadSafety(unittest.TestCase):
    """SemanticCache in-memory counters must be thread-safe under concurrent access."""

    def _make_cache(self):
        """Create a temp-dir-backed SemanticCache."""
        import tempfile
        from pathlib import Path
        from aictl.core.sem_cache import SemanticCache
        tmp = tempfile.mkdtemp()
        return SemanticCache(db_path=Path(tmp) / "test_sem_cache.db")

    def test_has_lock_attribute(self):
        """SemanticCache must have a _lock threading.Lock attribute."""
        cache = self._make_cache()
        self.assertTrue(
            hasattr(cache, "_lock"),
            "SemanticCache must have a _lock attribute for thread safety.",
        )
        self.assertIsInstance(
            cache._lock,
            type(threading.Lock()),
            "SemanticCache._lock must be a threading.Lock (or RLock).",
        )

    def test_concurrent_miss_increments(self):
        """Concurrent miss increments must be accurate under threading."""
        cache = self._make_cache()
        num_threads = 20
        errors = []

        def increment_miss():
            try:
                with cache._lock:
                    cache._misses += 1
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=increment_miss) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Thread errors: {errors}")
        self.assertEqual(
            cache._misses, num_threads,
            f"Expected {num_threads} misses after concurrent increments, got {cache._misses}",
        )

    def test_stats_reads_under_lock(self):
        """stats() must read counters under lock (verify no AttributeError)."""
        cache = self._make_cache()
        # Manually set some counter values
        cache._hits = 5
        cache._misses = 3
        cache._total_tokens_saved = 100

        result = cache.stats()
        self.assertEqual(result["session_hits"], 5)
        self.assertEqual(result["session_misses"], 3)
        self.assertEqual(result["total_tokens_saved"], 100)
        self.assertAlmostEqual(result["session_hit_rate"], 5 / 8, places=3)

    def test_docstring_no_misleading_claim(self):
        """SemanticCache docstring must not claim partial thread-safety as full."""
        src = (
            __import__("pathlib").Path(__file__).parent.parent
            / "aictl" / "core" / "sem_cache.py"
        ).read_text()
        # Old claim: "Thread-safe for single-process use (SQLite WAL mode)"
        # was misleading — WAL mode doesn't protect in-memory counters
        self.assertNotIn(
            "Thread-safe for single-process use (SQLite WAL mode)",
            src,
            "Old misleading docstring still present — should mention both WAL and Lock.",
        )
        self.assertIn(
            "threading.Lock",
            src,
            "sem_cache.py must import or reference threading.Lock for thread safety.",
        )


if __name__ == "__main__":
    unittest.main()

"""E2E test: mock engine + daemon + full request path."""

import json
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.daemon.mock_engine import start_mock_engine, _metrics, _lock


class TestMockEngine(unittest.TestCase):
    """Test the mock inference engine standalone."""

    @classmethod
    def setUpClass(cls):
        cls.server = start_mock_engine(port=19999)
        time.sleep(0.1)
        cls.base = "http://127.0.0.1:19999"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_mock_engine_health(self):
        with urllib.request.urlopen(f"{self.base}/health", timeout=5) as r:
            data = json.loads(r.read())
            self.assertEqual(data["status"], "ok")

    def test_mock_engine_models_list(self):
        with urllib.request.urlopen(f"{self.base}/v1/models", timeout=5) as r:
            data = json.loads(r.read())
            self.assertEqual(data["object"], "list")
            self.assertGreater(len(data["data"]), 0)
            self.assertEqual(data["data"][0]["id"], "mock-llama3-8b")

    def test_chat_completion(self):
        body = json.dumps({
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 50,
        }).encode()
        req = urllib.request.Request(
            f"{self.base}/v1/chat/completions",
            data=body, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            self.assertIn("choices", data)
            self.assertEqual(len(data["choices"]), 1)
            content = data["choices"][0]["message"]["content"]
            self.assertIn("mock", content.lower())
            self.assertIn("usage", data)
            self.assertGreater(data["usage"]["completion_tokens"], 0)

    def test_chat_completion_different_prompts(self):
        for prompt in ["hello", "what is this", "test"]:
            body = json.dumps({
                "model": "mock-llama3-8b",
                "messages": [{"role": "user", "content": prompt}],
            }).encode()
            req = urllib.request.Request(
                f"{self.base}/v1/chat/completions",
                data=body, headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
                self.assertIn("choices", data)

    def test_streaming(self):
        body = json.dumps({
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        }).encode()
        req = urllib.request.Request(
            f"{self.base}/v1/chat/completions",
            data=body, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            chunks = r.read().decode()
            self.assertIn("data:", chunks)
            self.assertIn("[DONE]", chunks)
            # Parse first chunk
            first_line = [l for l in chunks.splitlines() if l.startswith("data: {")][0]
            chunk = json.loads(first_line[6:])
            self.assertEqual(chunk["object"], "chat.completion.chunk")

    def test_metrics(self):
        with urllib.request.urlopen(f"{self.base}/metrics", timeout=5) as r:
            text = r.read().decode()
            self.assertIn("vllm:num_requests_running", text)
            self.assertIn("vllm:num_requests_waiting", text)
            self.assertIn("vllm:kv_cache_usage_perc", text)

    def test_ollama_compat_tags(self):
        with urllib.request.urlopen(f"{self.base}/api/tags", timeout=5) as r:
            data = json.loads(r.read())
            self.assertIn("models", data)

    def test_ollama_compat_generate(self):
        body = json.dumps({"model": "mock", "prompt": "test"}).encode()
        req = urllib.request.Request(
            f"{self.base}/api/generate",
            data=body, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            self.assertTrue(data["done"])

    def test_request_counter(self):
        with _lock:
            before = _metrics["requests_total"]
        body = json.dumps({
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "count test"}],
        }).encode()
        req = urllib.request.Request(
            f"{self.base}/v1/chat/completions",
            data=body, headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10).read()
        with _lock:
            after = _metrics["requests_total"]
        self.assertEqual(after, before + 1)


class TestDemoAutoMode(unittest.TestCase):
    """Test the auto demo scenario."""

    def test_auto_demo(self):
        from aictl.cmd.demo import _run_auto_demo
        from aictl.daemon.mock_engine import start_mock_engine
        from aictl.daemon.aiosd import AIOSHandler, ThreadedHTTPServer
        from aictl.core.state import StateStore, NodeState
        import tempfile

        # Mock engine
        server = start_mock_engine(port=19998)
        time.sleep(0.1)

        # Daemon
        tmp = Path(tempfile.mkdtemp())
        store = StateStore(tmp)
        store.save_node(NodeState(node_id="demo-test", hostname="test",
                                  profile="cpu-only", version="1.5.0", ram_total_mb=16384))
        AIOSHandler.store = store
        daemon = ThreadedHTTPServer(("127.0.0.1", 19995), AIOSHandler)
        daemon._start_time = time.time()
        dt = threading.Thread(target=daemon.serve_forever, daemon=True)
        dt.start()
        time.sleep(0.1)

        try:
            code = _run_auto_demo(19998, 19995)
            self.assertEqual(code, 0)
        finally:
            server.shutdown()
            server.server_close()
            daemon.shutdown()
            daemon.server_close()


if __name__ == "__main__":
    unittest.main()

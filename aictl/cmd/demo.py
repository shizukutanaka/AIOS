"""aictl demo — run full stack in mock mode for demonstration.

Starts: mock engine + daemon + full demo scenario.
This proves the entire request path works without any real GPU or engine.
"""

from __future__ import annotations

from typing import Any

import argparse

import json
from aictl.core.constants import AICTL_VERSION, MOCK_ENGINE_PORT, DAEMON_PORT
import signal
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from aictl.core.output import ok, err
from aictl.core.state import StateStore, NodeState


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("demo", help="Run full stack demo (mock engine + daemon)")
    p.add_argument("--engine-port", type=int, default=MOCK_ENGINE_PORT)
    p.add_argument("--daemon-port", type=int, default=DAEMON_PORT)
    p.add_argument("--auto", action="store_true", help="Auto-run demo scenario then exit")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the demo command."""
    engine_port = getattr(args, "engine_port", MOCK_ENGINE_PORT)
    daemon_port = getattr(args, "daemon_port", DAEMON_PORT)
    auto = getattr(args, "auto", False)

    # Start mock engine
    from aictl.daemon.mock_engine import start_mock_engine
    ok(f"Starting mock inference engine on :{engine_port}")
    mock = start_mock_engine(port=engine_port)
    time.sleep(0.3)

    # Verify mock engine
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{engine_port}/health", timeout=2) as r:
            data = json.loads(r.read())
            if data.get("status") == "ok":
                ok("Mock engine healthy")
    except Exception as e:
        err(f"Mock engine failed: {e}")
        return 1

    # Start daemon
    from aictl.daemon.aiosd import AIOSHandler, ThreadedHTTPServer
    tmp = Path(tempfile.mkdtemp())
    store = StateStore(tmp)
    store.save_node(NodeState(
        node_id="demo", hostname="demo", profile="mock",
        version=AICTL_VERSION, ram_total_mb=16384,
    ))
    AIOSHandler.store = store
    try:
        daemon = ThreadedHTTPServer(("127.0.0.1", daemon_port), AIOSHandler)
        daemon._start_time = time.time()
        daemon_thread = threading.Thread(target=daemon.serve_forever, daemon=True)
        daemon_thread.start()
        ok(f"Daemon started on :{daemon_port}")
    except OSError:
        ok(f"Daemon port :{daemon_port} in use (reusing existing)")

    if auto:
        code = _run_auto_demo(engine_port, daemon_port)
        mock.shutdown()
        return code

    print()
    print(f"  Mock engine:  http://127.0.0.1:{engine_port}")
    print(f"  Daemon API:   http://127.0.0.1:{daemon_port}")
    print()
    print("  Try:")
    print(f"    curl http://127.0.0.1:{daemon_port}/v1/health")
    print(f'    curl -X POST http://127.0.0.1:{engine_port}/v1/chat/completions \\')
    print('      -H "Content-Type: application/json" \\')
    print('      -d \'{"model":"mock-llama3-8b","messages":[{"role":"user","content":"hello"}]}\'')
    print()
    print("  Press Ctrl+C to stop")

    try:
        signal.pause()
    except (KeyboardInterrupt, AttributeError):
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass  # best-effort; failure is non-critical

    mock.shutdown()
    return 0


def _run_auto_demo(engine_port: int, daemon_port: int) -> int:
    """Run automated demo: engine + daemon + full verification."""
    print()
    ok("Running full-stack demo")
    print()

    steps_ok = 0
    steps_total = 0

    # 1. List models via mock engine
    steps_total += 1
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{engine_port}/v1/models", timeout=5) as r:
            models = json.loads(r.read())
        names = [m["id"] for m in models.get("data", [])]
        print(f"  1. Models: {', '.join(names)}")
        steps_ok += 1
    except Exception as e:
        print(f"  1. Models: FAILED ({e})")

    # 2. Chat completion
    steps_total += 1
    try:
        body = json.dumps({
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 50,
        }).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{engine_port}/v1/chat/completions",
            data=body, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
        content = resp["choices"][0]["message"]["content"]
        tokens = resp["usage"]["completion_tokens"]
        print(f"  2. Chat: \"{content[:60]}...\" ({tokens} tokens)")
        steps_ok += 1
    except Exception as e:
        print(f"  2. Chat: FAILED ({e})")

    # 3. Streaming
    steps_total += 1
    try:
        body = json.dumps({
            "model": "mock-llama3-8b",
            "messages": [{"role": "user", "content": "test"}],
            "stream": True,
        }).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{engine_port}/v1/chat/completions",
            data=body, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            chunks = r.read().decode()
        chunk_count = chunks.count("data: {")
        print(f"  3. Streaming: {chunk_count} SSE chunks")
        steps_ok += 1
    except Exception as e:
        print(f"  3. Streaming: FAILED ({e})")

    # 4. Daemon health
    steps_total += 1
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{daemon_port}/v1/health", timeout=5) as r:
            health = json.loads(r.read())
        print(f"  4. Daemon: {health['status']} (profile: {health.get('profile', '?')})")
        steps_ok += 1
    except Exception as e:
        print(f"  4. Daemon: FAILED ({e})")

    # 5. Daemon fabric
    steps_total += 1
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{daemon_port}/v1/fabric", timeout=5) as r:
            fabric = json.loads(r.read())
        tiers = len(fabric["fabric"]["tiers"])
        total = fabric["fabric"]["total_capacity_gb"]
        print(f"  5. Fabric: {total:.1f} GB across {tiers} tiers")
        steps_ok += 1
    except Exception as e:
        print(f"  5. Fabric: FAILED ({e})")

    # 6. Daemon recommend
    steps_total += 1
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{daemon_port}/v1/recommend", timeout=5) as r:
            recs = json.loads(r.read())
        count = len(recs["recommendations"])
        print(f"  6. Recommend: {count} models for current hardware")
        steps_ok += 1
    except Exception as e:
        print(f"  6. Recommend: FAILED ({e})")

    # 7. Prometheus metrics
    steps_total += 1
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{engine_port}/metrics", timeout=5) as r:
            metrics = r.read().decode()
        line_count = len(metrics.strip().splitlines())
        print(f"  7. Metrics: {line_count} Prometheus lines")
        steps_ok += 1
    except Exception as e:
        print(f"  7. Metrics: FAILED ({e})")

    # 8. Dynamo status
    steps_total += 1
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{daemon_port}/v1/dynamo", timeout=5) as r:
            dynamo = json.loads(r.read())
        kvbm_dram = dynamo["kvbm"]["cpu_dram_gb"]
        print(f"  8. Dynamo/KVBM: {kvbm_dram:.1f} GB DRAM for KV cache")
        steps_ok += 1
    except Exception as e:
        print(f"  8. Dynamo: FAILED ({e})")

    print(f"\n  Result: {steps_ok}/{steps_total} steps passed")

    if steps_ok == steps_total:
        ok("Full-stack demo complete — all systems operational")
        return 0
    else:
        err(f"{steps_total - steps_ok} steps failed")
        return 1

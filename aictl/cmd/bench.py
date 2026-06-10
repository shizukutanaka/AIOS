"""aictl bench — benchmark inference engine performance."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, err, print_json, print_kv
from aictl.core.constants import OLLAMA_DEFAULT_PORT


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("bench", help="Benchmark inference performance")
    p.add_argument("--endpoint", default=f"http://localhost:{OLLAMA_DEFAULT_PORT}",
                   help="Inference endpoint URL")
    p.add_argument("--model", default="", help="Model name")
    p.add_argument("-n", "--requests", type=int, default=5, help="Number of requests")
    p.add_argument("--max-tokens", type=int, default=100, help="Max tokens per request")
    p.add_argument("--mock", action="store_true", help="Start mock engine and benchmark it")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the bench command."""
    from aictl.runtime.benchmark import run_benchmark
    import time

    endpoint = args.endpoint
    mock_server = None

    if getattr(args, "mock", False):
        from aictl.daemon.mock_engine import start_mock_engine
        from aictl.core.constants import TEST_BENCH_PORT
        mock_server = start_mock_engine(port=TEST_BENCH_PORT)
        time.sleep(0.3)
        endpoint = f"http://127.0.0.1:{TEST_BENCH_PORT}"
        ok("Mock engine started for benchmarking")

    ok(f"Benchmarking {endpoint} ({args.requests} requests)...")
    try:
        result = run_benchmark(
            endpoint=endpoint,
            model=args.model or "mock-llama3-8b",
            num_requests=args.requests,
            max_tokens=args.max_tokens,
        )

        if getattr(args, "json", False):
            print_json(result.__dict__)
            return 0

        if result.errors == result.requests:
            err(f"All {result.requests} requests failed — check endpoint")
            return 1

        ok(f"Benchmark complete ({result.requests - result.errors}/{result.requests} succeeded)")
        print()
        print_kv([
            ("Endpoint", result.endpoint),
            ("Model", result.model or "(default)"),
            ("TTFT avg", f"{result.ttft_ms_avg:.0f} ms"),
            ("TTFT p95", f"{result.ttft_ms_p95:.0f} ms"),
            ("Total avg", f"{result.total_ms_avg:.0f} ms"),
            ("Tokens", str(result.tokens_generated)),
            ("Throughput", f"{result.tokens_per_sec:.1f} tokens/sec"),
            ("Duration", f"{result.duration_sec:.1f}s"),
            ("Errors", str(result.errors)),
        ])
        return 0
    finally:
        if mock_server:
            mock_server.shutdown()

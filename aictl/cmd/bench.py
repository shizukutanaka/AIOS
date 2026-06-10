"""aictl bench — benchmark inference engine performance."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, err, warn, print_json, print_kv, print_table
from aictl.core.constants import OLLAMA_DEFAULT_PORT


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("bench", help="Benchmark inference performance")
    bsub = p.add_subparsers(dest="bench_cmd")

    # Default single-engine bench (kept at top level for backwards compat)
    p.add_argument("--endpoint", default=f"http://localhost:{OLLAMA_DEFAULT_PORT}",
                   help="Inference endpoint URL")
    p.add_argument("--model", default="", help="Model name")
    p.add_argument("-n", "--requests", type=int, default=5, help="Number of requests")
    p.add_argument("--max-tokens", type=int, default=100, help="Max tokens per request")
    p.add_argument("--mock", action="store_true", help="Start mock engine and benchmark it")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=run)

    # Compare subcommand
    cmp = bsub.add_parser("compare", help="Compare two inference endpoints side-by-side")
    cmp.add_argument("endpoints", nargs="+", metavar="URL",
                     help="Two or more endpoint URLs to compare")
    cmp.add_argument("--model", default="", help="Model name (same for both)")
    cmp.add_argument("-n", "--requests", type=int, default=5, help="Requests per engine")
    cmp.add_argument("--max-tokens", type=int, default=100, help="Max tokens per request")
    cmp.add_argument("--json", action="store_true", help="JSON output")
    cmp.set_defaults(func=run_compare)


def run_compare(args: argparse.Namespace) -> int:
    """Execute the bench compare subcommand."""
    from aictl.runtime.benchmark import run_benchmark, BenchResult

    endpoints = args.endpoints
    if len(endpoints) < 2:
        err("bench compare requires at least 2 endpoint URLs")
        return 1

    results = []
    for ep in endpoints:
        try:
            r = run_benchmark(
                endpoint=ep,
                model=args.model or "mock-llama3-8b",
                num_requests=args.requests,
                max_tokens=args.max_tokens,
            )
            results.append(r)
        except Exception as exc:
            warn(f"Failed to benchmark {ep}: {exc}")
            results.append(BenchResult(
                endpoint=ep,
                model=args.model or "mock-llama3-8b",
                requests=args.requests,
                errors=args.requests,
            ))

    if getattr(args, "json", False):
        print_json([r.__dict__ for r in results])
        return 0

    # Determine winner by throughput (tokens/sec), excluding all-error endpoints
    valid = [r for r in results if r.errors < r.requests]
    winner_ep = max(valid, key=lambda r: r.tokens_per_sec).endpoint if valid else None

    rows = []
    for r in results:
        tag = " *" if r.endpoint == winner_ep else ""
        rows.append({
            "endpoint": r.endpoint + tag,
            "ttft_avg": f"{r.ttft_ms_avg:.0f}ms",
            "ttft_p95": f"{r.ttft_ms_p95:.0f}ms",
            "tok/sec": f"{r.tokens_per_sec:.1f}",
            "errors": f"{r.errors}/{r.requests}",
        })

    print()
    print_table(rows, ["endpoint", "ttft_avg", "ttft_p95", "tok/sec", "errors"])
    if winner_ep:
        ok(f"Winner: {winner_ep} (highest throughput)")
    return 0


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

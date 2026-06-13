"""aictl bench — benchmark inference engine performance."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, err, warn, print_json, print_kv, print_table
from aictl.core.constants import OLLAMA_DEFAULT_PORT
from aictl.runtime.benchmark import run_benchmark, BenchResult


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

    # SLO verification subcommand
    slo = bsub.add_parser("slo", help="Verify endpoint meets configured SLO thresholds")
    slo.add_argument("endpoint", help="Inference endpoint URL")
    slo.add_argument("--model", default="", help="Model name")
    slo.add_argument("-n", "--requests", type=int, default=5, help="Number of requests")
    slo.set_defaults(func=run_slo)

    # Baseline subcommand
    baseline = bsub.add_parser("baseline", help="Show expected baselines for common models")
    baseline.set_defaults(func=run_baseline)

    # Compare subcommand
    cmp = bsub.add_parser("compare", help="Compare two inference endpoints side-by-side")
    cmp.add_argument("endpoints", nargs="+", metavar="URL",
                     help="Two or more endpoint URLs to compare")
    cmp.add_argument("--model", default="", help="Model name (same for both)")
    cmp.add_argument("-n", "--requests", type=int, default=5, help="Requests per engine")
    cmp.add_argument("--max-tokens", type=int, default=100, help="Max tokens per request")
    cmp.add_argument("--json", action="store_true", help="JSON output")
    cmp.set_defaults(func=run_compare)

    hist = bsub.add_parser("history", help="Show recent benchmark results from perf records")
    hist.add_argument("-n", "--last", type=int, default=20, help="Number of records to show")
    hist.set_defaults(func=run_history)


def run_history(args: argparse.Namespace) -> int:
    """Show recent inference timing records from the perf store."""
    from aictl.core.perf import read_recent
    import time as _time
    records = read_recent(limit=getattr(args, "last", 20))

    if not records:
        if getattr(args, "json", False):
            print_json([])
            return 0
        print("No performance history. Run benchmarks or make inference calls first.")
        return 0

    if getattr(args, "json", False):
        print_json([{
            "command": r.command, "duration_ms": r.duration_ms,
            "exit_code": r.exit_code, "rss_mb_peak": r.rss_mb_peak,
            "ts": _time.strftime("%Y-%m-%dT%H:%M:%S", _time.localtime(r.timestamp)),
        } for r in records])
        return 0

    rows = [{
        "time": _time.strftime("%H:%M:%S", _time.localtime(r.timestamp)),
        "command": r.command[:35],
        "duration_ms": f"{r.duration_ms:.0f}",
        "exit": str(r.exit_code),
        "rss_mb": f"{r.rss_mb_peak:.0f}",
    } for r in records]
    print_table(rows, ["time", "command", "duration_ms", "exit", "rss_mb"])
    return 0


def run_slo(args: argparse.Namespace) -> int:
    """Run a benchmark and verify results against SLO thresholds."""
    from aictl.metrics.slo import SLOTarget

    slo = SLOTarget()
    ok(f"SLO verification: {args.endpoint}")
    try:
        result = run_benchmark(
            endpoint=args.endpoint,
            model=args.model or "mock-llama3-8b",
            num_requests=getattr(args, "requests", 5),
            max_tokens=100,
        )
    except Exception as exc:
        err(f"Benchmark failed: {exc}")
        return 1

    checks = [
        ("TTFT p95", result.ttft_ms_p95, slo.ttft_p95_ms, "≤",
         result.ttft_ms_p95 <= slo.ttft_p95_ms),
        ("tokens/sec", result.tokens_per_sec, slo.tokens_per_sec_min, "≥",
         result.tokens_per_sec >= slo.tokens_per_sec_min),
        ("error_rate", result.errors / max(result.requests, 1),
         slo.error_rate_max, "≤",
         result.errors / max(result.requests, 1) <= slo.error_rate_max),
    ]

    passed = sum(1 for *_, ok_flag in checks if ok_flag)

    if getattr(args, "json", False):
        print_json({
            "endpoint": result.endpoint,
            "slo_passed": passed == len(checks),
            "checks": [{"metric": m, "value": v, "threshold": t, "op": op, "pass": p}
                       for m, v, t, op, p in checks],
        })
        return 0 if passed == len(checks) else 1

    rows = [{"metric": m, "value": f"{v:.1f}", "threshold": f"{op} {t:.1f}",
             "pass": "✓" if p else "✗"}
            for m, v, t, op, p in checks]
    print_table(rows, ["metric", "value", "threshold", "pass"])
    if passed == len(checks):
        ok("SLO: PASS")
    else:
        err(f"SLO: FAIL ({passed}/{len(checks)} checks passed)")
    return 0 if passed == len(checks) else 1


# Known reference baselines for common hardware + model combinations
_BASELINES = [
    {"model": "llama3.2:3b",  "hw": "RTX 4090",  "ttft_ms_p95": 50,   "tok_s": 120},
    {"model": "llama3.1:8b",  "hw": "RTX 4090",  "ttft_ms_p95": 150,  "tok_s": 60},
    {"model": "llama3.1:70b", "hw": "H100 (2x)", "ttft_ms_p95": 300,  "tok_s": 35},
    {"model": "mixtral:8x7b", "hw": "A100 (2x)", "ttft_ms_p95": 200,  "tok_s": 45},
    {"model": "phi3:mini",    "hw": "M2 Pro CPU", "ttft_ms_p95": 800,  "tok_s": 12},
    {"model": "llama3.2:1b",  "hw": "CPU (16c)",  "ttft_ms_p95": 2000, "tok_s": 6},
]


def run_baseline(args: argparse.Namespace) -> int:
    """Show expected performance baselines for common model/hardware combos."""
    if getattr(args, "json", False):
        print_json(_BASELINES)
        return 0

    ok("Reference baselines (community measured)")
    print_table(_BASELINES, ["model", "hw", "ttft_ms_p95", "tok_s"])
    print("\n  Compare with: aictl bench --endpoint <url> --model <name>")
    return 0


def run_compare(args: argparse.Namespace) -> int:
    """Execute the bench compare subcommand."""

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

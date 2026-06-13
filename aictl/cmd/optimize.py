"""aictl optimize — inference performance tuning advisor.

Gathers live metrics from all reachable engines, compares against SLO
targets, and emits ranked tuning recommendations with impact/effort labels.
"""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, print_json, print_table
from aictl.runtime.adapters import discover_engines, get_adapter


_SEVERITY = {"high": 3, "medium": 2, "low": 1}


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser(
        "optimize",
        help="Analyze inference metrics and recommend performance tuning",
    )
    p.add_argument("--engine", default="", help="Target a specific engine (vllm/ollama/sglang)")
    p.add_argument("--top", type=int, default=5, help="Show top N recommendations (default 5)")
    p.set_defaults(func=run)


def _analyze_engine(engine: str, endpoint: str, slo: Any) -> list[dict[str, Any]]:
    """Gather metrics from one engine and return recommendations."""
    recs: list[dict[str, Any]] = []

    adapter = get_adapter(engine, endpoint)
    if not adapter:
        return recs

    try:
        health = adapter.health()
        if not health.reachable:
            return recs
        metrics = adapter.scrape_metrics()
    except Exception:
        return recs

    ttft = metrics.ttft_ms_p95
    itl = metrics.itl_ms_p95
    kv = metrics.kv_cache_utilization
    queue = metrics.queue_depth
    throughput = metrics.throughput_tokens_per_sec

    # High TTFT → try quantization or speculative decoding
    if ttft > slo.ttft_p95_ms:
        recs.append({
            "engine": engine,
            "metric": f"ttft_p95={ttft:.0f}ms > SLO {slo.ttft_p95_ms:.0f}ms",
            "recommendation": "Enable INT4/AWQ quantization to reduce TTFT",
            "command": f"aictl quant --model <model> --bits 4",
            "impact": "high",
            "effort": "low",
        })
        recs.append({
            "engine": engine,
            "metric": f"ttft_p95={ttft:.0f}ms",
            "recommendation": "Enable speculative decoding with a small draft model",
            "command": f"aictl spec --engine {engine} --draft-model <draft>",
            "impact": "medium",
            "effort": "medium",
        })

    # High KV cache utilization → increase max cache or reduce concurrency
    if kv > slo.kv_cache_max:
        recs.append({
            "engine": engine,
            "metric": f"kv_cache={kv:.1%} > SLO {slo.kv_cache_max:.1%}",
            "recommendation": "Increase --gpu-memory-utilization or add replicas",
            "command": f"aictl scale keda <deployment> --max 4",
            "impact": "high",
            "effort": "low",
        })

    # High queue depth → scale up
    if queue > slo.queue_depth_max:
        recs.append({
            "engine": engine,
            "metric": f"queue_depth={queue} > SLO {slo.queue_depth_max}",
            "recommendation": "Scale up replicas to absorb request backlog",
            "command": f"aictl scale keda <deployment> --min 2 --max 8",
            "impact": "high",
            "effort": "low",
        })

    # Low throughput → batching optimization
    if 0 < throughput < slo.tokens_per_sec_min:
        recs.append({
            "engine": engine,
            "metric": f"throughput={throughput:.0f} tok/s < SLO {slo.tokens_per_sec_min:.0f}",
            "recommendation": "Increase max-batch-prefill-tokens or use continuous batching",
            "command": "aictl deploy optimize <model> --gpu <gpu-type>",
            "impact": "medium",
            "effort": "medium",
        })

    # High ITL → reduce concurrency or batch size
    if itl > slo.itl_p95_ms:
        recs.append({
            "engine": engine,
            "metric": f"itl_p95={itl:.0f}ms > SLO {slo.itl_p95_ms:.0f}ms",
            "recommendation": "Reduce --max-num-seqs or enable chunked prefill",
            "command": "Update engine launch flags via aictl deploy optimize",
            "impact": "medium",
            "effort": "medium",
        })

    # No issues
    if not recs and health.reachable:
        recs.append({
            "engine": engine,
            "metric": "all metrics within SLO",
            "recommendation": "System is performing within SLO targets",
            "command": "",
            "impact": "low",
            "effort": "none",
        })

    return recs


def run(args: argparse.Namespace) -> int:
    """Analyze metrics and surface the top tuning recommendations."""
    from pathlib import Path
    from aictl.core.config import load_config

    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    config = load_config(state_dir)
    slo = config.slo
    endpoints = config.engines.to_dict()

    engine_filter = getattr(args, "engine", "")
    healths = discover_engines(endpoints)
    if engine_filter:
        healths = [h for h in healths if h.engine == engine_filter]

    all_recs: list[dict[str, Any]] = []
    for h in healths:
        all_recs.extend(_analyze_engine(h.engine, h.endpoint, slo))

    # Sort by impact (high → medium → low), then engine name
    all_recs.sort(
        key=lambda r: (-_SEVERITY.get(r["impact"], 0), r["engine"]),
    )

    top = getattr(args, "top", 5)
    shown = all_recs[:top]

    if getattr(args, "json", False):
        print_json(shown)
        return 0

    if not shown:
        ok("No engines reachable — cannot generate recommendations.")
        return 0

    print(f"  Top {len(shown)} optimization recommendation(s):\n")
    for i, r in enumerate(shown, 1):
        print(f"  {i}. [{r['impact'].upper()}] {r['engine']}: {r['metric']}")
        print(f"     → {r['recommendation']}")
        if r.get("command"):
            print(f"       {r['command']}")
        print()
    return 0

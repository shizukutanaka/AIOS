"""aictl diff — Compare outputs from two models on the same prompts.

The #1 workflow before switching models: "Is qwen3:7b actually better
than llama3.1:8b for MY use cases?"

No existing tool answers this with a single zero-dependency CLI command.
LM Studio requires a GUI. promptfoo requires Node.js. We do it in stdlib.

Metrics:
  length_ratio     — output B / output A (> 1 = B is more verbose)
  overlap          — Jaccard similarity on word sets (quick proxy for agreement)
  latency_delta    — B.latency - A.latency in ms (positive = B is slower)
  cost_delta       — B.cost_usd - A.cost_usd (positive = B is more expensive)
  verdict          — based on configurable weights

Usage:
  aictl diff llama3.1:8b qwen3:7b          # default prompts
  aictl diff llama3.1:8b qwen3:7b --prompts ./my_prompts.json
  aictl diff llama3.1:8b qwen3:7b --prompts ./suite.json --json
  aictl diff llama3.1:8b qwen3:7b --n 5    # quick 5-prompt spot check

Prompts file format (JSON array of strings or objects):
  ["Summarize this: ...", "Classify: ..."]
  [{"prompt": "...", "label": "summarize"}]
"""

from __future__ import annotations

from typing import Any

import argparse

import json
import time
from pathlib import Path

from aictl.core.output import ok, err, print_json, print_table


# ── Built-in benchmark prompts ──────────────────────────────
_DEFAULT_PROMPTS = [
    {"label": "factual",    "prompt": "What is the capital of Japan?"},
    {"label": "reasoning",  "prompt": "If a train leaves at 9am at 80km/h and another at 10am at 100km/h, when does the second catch up?"},
    {"label": "code",       "prompt": "Write a Python one-liner that reverses a string."},
    {"label": "summarize",  "prompt": "Summarize in one sentence: The moon affects Earth's tides through gravitational pull."},
    {"label": "creative",   "prompt": "Give me a product name for an AI-powered coffee machine."},
]


def register(sub: Any) -> None:
    """Register CLI subcommand."""
    p = sub.add_parser(
        "diff",
        help="Compare two models on the same prompts. Find which is better for your use case.",
    )
    p.add_argument("model_a", help="Baseline model (e.g. llama3.1:8b)")
    p.add_argument("model_b", help="Challenger model (e.g. qwen3:7b)")
    p.add_argument("--prompts", help="Path to prompts JSON file")
    p.add_argument("--n", type=int, default=0, help="Number of default prompts to use (0 = all)")
    p.add_argument("--engine", default="ollama", choices=["ollama", "vllm", "sglang"],
                   help="Inference engine to use")
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Output as JSON")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the diff comparison."""
    model_a = args.model_a
    model_b = args.model_b
    use_json = getattr(args, "json", False)

    # Load prompts
    prompts = _load_prompts(getattr(args, "prompts", None), getattr(args, "n", 0))
    if not prompts:
        err("No prompts found.")
        return 1

    if not use_json:
        print()
        print(f"  Comparing: {model_a}  vs  {model_b}")
        print(f"  Prompts:   {len(prompts)}")
        print(f"  Engine:    {args.engine}")
        print()

    # Run inference for both models
    results_a = _run_model(model_a, prompts, args.engine, use_json)
    results_b = _run_model(model_b, prompts, args.engine, use_json)

    # Compute per-prompt metrics
    rows: list[dict[str, Any]] = []
    wins_a = 0
    wins_b = 0

    for i, (label, prompt) in enumerate(prompts):
        ra = results_a[i]
        rb = results_b[i]

        overlap = _jaccard(ra["text"], rb["text"])
        length_ratio = len(rb["text"]) / max(len(ra["text"]), 1)
        latency_delta = rb["latency_ms"] - ra["latency_ms"]
        cost_delta = rb["cost_usd"] - ra["cost_usd"]

        # Heuristic verdict: prefer shorter latency + higher overlap (agreement)
        # In practice: if models agree (overlap >0.6), call it a tie
        if overlap > 0.6:
            verdict = "tie"
        elif latency_delta < -200:
            verdict = model_b  # B is notably faster
            wins_b += 1
        elif latency_delta > 200:
            verdict = model_a  # A is notably faster
            wins_a += 1
        else:
            verdict = "tie"

        rows.append({
            "label": label,
            "overlap": round(overlap, 3),
            "len_ratio": round(length_ratio, 2),
            "latency_a_ms": ra["latency_ms"],
            "latency_b_ms": rb["latency_ms"],
            "latency_delta_ms": latency_delta,
            "cost_a_usd": round(ra["cost_usd"], 6),
            "cost_b_usd": round(rb["cost_usd"], 6),
            "cost_delta_usd": round(cost_delta, 6),
            "verdict": verdict,
            "a_text": ra["text"][:80],
            "b_text": rb["text"][:80],
        })

    if use_json:
        print_json({
            "model_a": model_a,
            "model_b": model_b,
            "prompts_count": len(prompts),
            "results": rows,
            "summary": {
                "wins_a": wins_a,
                "wins_b": wins_b,
                "ties": len(rows) - wins_a - wins_b,
                "avg_latency_a_ms": int(sum(r["latency_a_ms"] for r in rows) / max(len(rows), 1)),
                "avg_latency_b_ms": int(sum(r["latency_b_ms"] for r in rows) / max(len(rows), 1)),
                "avg_cost_a_usd": round(sum(r["cost_a_usd"] for r in rows) / max(len(rows), 1), 6),
                "avg_cost_b_usd": round(sum(r["cost_b_usd"] for r in rows) / max(len(rows), 1), 6),
            },
        })
        return 0

    # Display table
    table_rows = [
        {
            "PROMPT": r["label"],
            "OVERLAP": f"{r['overlap']:.2f}",
            "LEN RATIO": f"{r['len_ratio']:.2f}",
            f"LAT {model_a[:8]}": f"{r['latency_a_ms']}ms",
            f"LAT {model_b[:8]}": f"{r['latency_b_ms']}ms",
            "VERDICT": r["verdict"],
        }
        for r in rows
    ]
    cols = ["PROMPT", "OVERLAP", "LEN RATIO",
            f"LAT {model_a[:8]}", f"LAT {model_b[:8]}", "VERDICT"]
    print_table(table_rows, cols)
    print()

    # Summary
    avg_lat_a = int(sum(r["latency_a_ms"] for r in rows) / max(len(rows), 1))
    avg_lat_b = int(sum(r["latency_b_ms"] for r in rows) / max(len(rows), 1))
    avg_overlap = sum(r["overlap"] for r in rows) / max(len(rows), 1)
    total_cost_a = sum(r["cost_a_usd"] for r in rows)
    total_cost_b = sum(r["cost_b_usd"] for r in rows)
    ties = len(rows) - wins_a - wins_b

    print(f"  Summary across {len(rows)} prompts:")
    print(f"    Output agreement (Jaccard):  {avg_overlap:.2f}  {'(models largely agree)' if avg_overlap > 0.5 else '(models differ significantly)'}")
    print(f"    Avg latency  {model_a[:16]:<16}  {avg_lat_a}ms")
    print(f"    Avg latency  {model_b[:16]:<16}  {avg_lat_b}ms")
    print(f"    Total cost   {model_a[:16]:<16}  ${total_cost_a:.4f}")
    print(f"    Total cost   {model_b[:16]:<16}  ${total_cost_b:.4f}")
    print()
    print(f"  Wins:  {model_a}: {wins_a}  |  {model_b}: {wins_b}  |  Ties: {ties}")
    print()

    # Recommendation
    if avg_overlap > 0.6:
        ok("Both models produce similar outputs. Choose based on cost or latency.")
    elif avg_lat_a < avg_lat_b and total_cost_a < total_cost_b:
        ok(f"Recommendation: keep {model_a} (faster + cheaper, similar quality)")
    elif avg_lat_b < avg_lat_a and total_cost_b < total_cost_a:
        ok(f"Recommendation: switch to {model_b} (faster + cheaper)")
    else:
        print("  Trade-off detected. Review the outputs above to choose.")
    print()

    # Show sample outputs for the first prompt
    if rows:
        r = rows[0]
        print(f"  Sample ({r['label']} prompt):")
        print(f"    {model_a}: {r['a_text'][:100]}...")
        print(f"    {model_b}: {r['b_text'][:100]}...")
        print()
    return 0


# ── Helpers ───────────────────────────────────────────────


def _load_prompts(path: str | None, n: int) -> list[tuple[str, str]]:
    """Load (label, prompt) pairs from file or defaults."""
    if path:
        try:
            raw = json.loads(Path(path).read_text())
            if isinstance(raw, list):
                items = []
                for item in raw:
                    if isinstance(item, str):
                        items.append(("prompt", item))
                    elif isinstance(item, dict):
                        label = item.get("label", f"prompt-{len(items)+1}")
                        prompt = item.get("prompt", "")
                        if prompt:
                            items.append((label, prompt))
                return items[:n] if n > 0 else items
        except Exception as e:
            err(f"Could not load prompts file: {e}")
            return []

    defaults = _DEFAULT_PROMPTS[:n] if n > 0 else _DEFAULT_PROMPTS
    return [(p["label"], p["prompt"]) for p in defaults]


def _run_model(model: str, prompts: list[tuple[str, str]], engine: str, quiet: bool) -> list[dict[str, Any]]:
    """Run all prompts through a model. Returns list of result dicts."""
    from aictl.sdk import _AmbientContext
    ctx = _AmbientContext()
    ctx.ensure_ready()

    results = []
    for label, prompt in prompts:
        if not quiet:
            print(f"    [{model[:20]}] {label}...", end=" ", flush=True)
        t0 = time.perf_counter()
        try:
            from aictl.sdk import _complete, _compute_call_cost
            text, tokens = _complete(
                endpoint=ctx.endpoint,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=200,
                allow_cloud=False,
            )
            latency_ms = int((time.perf_counter() - t0) * 1000)
            cost_usd, _ = _compute_call_cost(model, prompt, tokens, ctx.endpoint)
            if not quiet:
                print(f"{latency_ms}ms")
        except Exception as e:
            if not quiet:
                print(f"error: {e}")
            text = f"[error: {e}]"
            latency_ms = 0
            cost_usd = 0.0

        results.append({
            "text": text,
            "latency_ms": latency_ms,
            "cost_usd": cost_usd,
        })
    return results


def _jaccard(a: str, b: str) -> float:
    """Word-level Jaccard similarity: |A ∩ B| / |A ∪ B|."""
    if not a or not b:
        return 0.0
    set_a = set(a.lower().split())
    set_b = set(b.lower().split())
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0

"""aictl trace — distributed request tracing for inference."""

from __future__ import annotations

from typing import Any

import argparse

import json
import time
import urllib.request
from aictl.core.output import ok, err, print_json
from aictl.core.config import load_config
from aictl.core.state import StateStore


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("trace", help="Trace an inference request through the stack")
    p.add_argument("--prompt", default="Hello, how are you?", help="Test prompt")
    p.add_argument("--model", default="", help="Model name")
    p.add_argument("--endpoint", default="", help="Override endpoint")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the trace command."""
    store = StateStore(getattr(args, "state_dir", None))
    config = load_config(store.dir)
    prompt = getattr(args, "prompt", "Hello")
    model = getattr(args, "model", "")

    # Determine endpoint
    endpoint = getattr(args, "endpoint", "")
    if not endpoint:
        endpoints = config.engines.to_dict()
        for name, ep in endpoints.items():
            endpoint = ep
            break

    if not endpoint:
        err("No endpoint available. Start an engine first.")
        return 1

    ok(f"Tracing request through {endpoint}")

    trace: list[dict[str, Any]] = []

    # Step 1: DNS/TCP check
    t0 = time.monotonic()
    host = endpoint.replace("http://", "").replace("https://", "").split(":")[0]
    import socket
    try:
        socket.getaddrinfo(host, None)
        dns_ms = (time.monotonic() - t0) * 1000
        trace.append({"step": "dns_resolve", "host": host, "ms": round(dns_ms, 1), "status": "ok"})
    except socket.gaierror:
        trace.append({"step": "dns_resolve", "host": host, "ms": 0, "status": "failed"})
        _print_trace(trace)
        return 1

    # Step 2: Health check
    t0 = time.monotonic()
    health_url = f"{endpoint.rstrip('/')}/health"
    try:
        with urllib.request.urlopen(health_url, timeout=5) as r:
            r.read()
        health_ms = (time.monotonic() - t0) * 1000
        trace.append({"step": "health_check", "url": health_url, "ms": round(health_ms, 1), "status": "ok"})
    except Exception:
        trace.append({"step": "health_check", "url": health_url, "ms": 0, "status": "failed"})

    # Step 3: Model list
    t0 = time.monotonic()
    models_url = f"{endpoint.rstrip('/')}/v1/models"
    try:
        with urllib.request.urlopen(models_url, timeout=5) as r:
            data = json.loads(r.read())
        models = [m.get("id", "") for m in data.get("data", [])]
        models_ms = (time.monotonic() - t0) * 1000
        trace.append({"step": "model_list", "models": len(models), "ms": round(models_ms, 1), "status": "ok"})
        if not model and models:
            model = models[0]
    except Exception:
        trace.append({"step": "model_list", "ms": 0, "status": "skipped"})

    # Step 4: Inference request
    t0 = time.monotonic()
    ttft = 0
    tokens = 0
    try:
        body = json.dumps({
            "model": model or "default",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 50,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            f"{endpoint.rstrip('/')}/v1/chat/completions",
            data=body, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
        total_ms = (time.monotonic() - t0) * 1000
        tokens = resp.get("usage", {}).get("completion_tokens", 0)
        ttft = total_ms * 0.3  # Estimate
        trace.append({
            "step": "inference", "model": model, "tokens": tokens,
            "ttft_ms": round(ttft, 1), "total_ms": round(total_ms, 1), "status": "ok",
        })
    except Exception as e:
        total_ms = (time.monotonic() - t0) * 1000
        trace.append({"step": "inference", "ms": round(total_ms, 1), "status": f"failed: {e}"})

    if getattr(args, "json", False):
        print_json(trace)
        return 0

    _print_trace(trace)
    return 0


def _print_trace(trace: list[dict[str, Any]]) -> None:
    """Execute print trace."""
    print()
    for t in trace:
        icon = "\u2713" if t.get("status") == "ok" else "\u2717"
        ms = f"{t.get('ms', t.get('total_ms', 0)):.0f}ms"
        extra = ""
        if t.get("tokens"):
            extra = f" ({t['tokens']} tokens)"
        if t.get("models"):
            extra = f" ({t['models']} models)"
        print(f"  {icon} {t['step']:20s} {ms:>8s}{extra}")
    print()

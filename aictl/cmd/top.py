"""aictl top — live GPU + loaded-model resource monitor."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import print_json, print_table
from aictl.core.state import StateStore
from aictl.core.config import load_config


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("top", help="Live GPU and loaded-model resource monitor")
    p.add_argument("--watch", action="store_true", help="Continuously refresh")
    p.add_argument("--interval", type=int, default=2,
                   help="Refresh interval in seconds when --watch (default: 2)")
    p.add_argument("--json", action="store_true", help="JSON output (single snapshot)")
    p.set_defaults(func=run)


def _collect() -> dict:
    """Collect one snapshot of GPU stats + loaded models."""
    from aictl.runtime.broker import gpu_live_stats
    gpus = gpu_live_stats()
    models = _loaded_models()
    return {"gpus": gpus, "models": models}


def _loaded_models() -> list[dict]:
    """Query loaded models across configured engines (best-effort)."""
    import json
    import urllib.request

    store = StateStore()
    config = load_config(store.dir)
    engines = config.engines.to_dict()
    loaded: list[dict] = []

    for engine_name, base_url in engines.items():
        base_url = base_url.rstrip("/")
        if "11434" in base_url or engine_name == "ollama":
            try:
                with urllib.request.urlopen(f"{base_url}/api/ps", timeout=2) as r:
                    data = json.loads(r.read())
                for m in data.get("models", []):
                    loaded.append({
                        "engine": engine_name,
                        "model": m.get("name", ""),
                        "vram_mb": m.get("size_vram", 0) // (1024 * 1024),
                    })
            except Exception:
                pass
            continue
        try:
            req = urllib.request.Request(f"{base_url}/v1/models",
                                         headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=2) as r:
                data = json.loads(r.read())
            for m in data.get("data", []):
                loaded.append({"engine": engine_name, "model": m.get("id", ""), "vram_mb": 0})
        except Exception:
            pass
    return loaded


def _render(snapshot: dict) -> None:
    """Render one snapshot of the top display."""
    gpus = snapshot["gpus"]
    models = snapshot["models"]

    if gpus:
        print("GPUs")
        rows = []
        for g in gpus:
            mem_pct = (g["mem_used_mb"] / g["mem_total_mb"] * 100) if g["mem_total_mb"] else 0
            rows.append({
                "idx": str(g["index"]),
                "name": g["name"][:24],
                "util%": f"{g['util_pct']}%",
                "mem": f"{g['mem_used_mb']}/{g['mem_total_mb']}MB ({mem_pct:.0f}%)",
                "temp": f"{g['temp_c']}°C",
                "power": f"{g['power_w']:.0f}W",
            })
        print_table(rows, ["idx", "name", "util%", "mem", "temp", "power"])
    else:
        print("GPUs: none detected (nvidia-smi unavailable)")

    print()
    if models:
        print("Loaded models")
        print_table(models, ["engine", "model", "vram_mb"])
    else:
        print("Loaded models: none (no engines reachable)")


def run(args: argparse.Namespace) -> int:
    """Execute the top command."""
    if getattr(args, "json", False):
        print_json(_collect())
        return 0

    if getattr(args, "watch", False):
        import time
        import os
        interval = max(1, getattr(args, "interval", 2))
        try:
            while True:
                os.system("clear" if os.name != "nt" else "cls")
                _render(_collect())
                print(f"\n  Refreshing every {interval}s — Ctrl-C to stop")
                time.sleep(interval)
        except KeyboardInterrupt:
            return 0
        return 0

    _render(_collect())
    return 0

"""aictl config — view and modify persistent configuration."""

from __future__ import annotations

from typing import Any

import argparse

from pathlib import Path

from aictl.core.output import ok, err, print_json
from aictl.core.config import Config, load_config, save_config


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("config", help="View or modify configuration")
    csub = p.add_subparsers(dest="config_cmd")

    show = csub.add_parser("show", help="Show current config")
    show.set_defaults(func=run_show)

    get = csub.add_parser("get", help="Get a single config value by key")
    get.add_argument("key", help="Dot-separated key (e.g. engines.vllm)")
    get.add_argument("--json", action="store_true", help="JSON output")
    get.set_defaults(func=run_get)

    st = csub.add_parser("set", help="Set a config value")
    st.add_argument("key", help="Dot-separated key (e.g. engines.vllm)")
    st.add_argument("value", help="New value")
    st.set_defaults(func=run_set)

    reset = csub.add_parser("reset", help="Reset config to defaults")
    reset.set_defaults(func=run_reset)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_show(args: argparse.Namespace) -> int:
    """Execute the show subcommand."""
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    config = load_config(state_dir)
    from dataclasses import asdict
    if getattr(args, "json", False):
        print_json(asdict(config))
        return 0

    d = asdict(config)
    _print_nested(d)
    return 0


def run_get(args: argparse.Namespace) -> int:
    """Execute the get subcommand — retrieve a single config value by dot-key."""
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    config = load_config(state_dir)
    from dataclasses import asdict

    parts = args.key.split(".")
    obj: Any = asdict(config)
    for part in parts:
        if isinstance(obj, dict) and part in obj:
            obj = obj[part]
        else:
            err(f"Unknown key: {args.key}")
            if getattr(args, "json", False):
                print_json({"key": args.key, "found": False})
            return 1

    if getattr(args, "json", False):
        print_json({"key": args.key, "value": obj, "found": True})
        return 0

    if isinstance(obj, dict):
        _print_nested(obj)
    else:
        print(obj)
    return 0


def run_set(args: argparse.Namespace) -> int:
    """Execute the set subcommand."""
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    config = load_config(state_dir)
    from dataclasses import asdict

    parts = args.key.split(".")
    d = asdict(config)

    # Navigate to parent
    obj = d
    for part in parts[:-1]:
        if part not in obj or not isinstance(obj[part], dict):
            err(f"Unknown key: {args.key}")
            return 1
        obj = obj[part]

    last = parts[-1]
    if last not in obj:
        err(f"Unknown key: {args.key}")
        return 1

    # Type coerce
    old_val = obj[last]
    try:
        if isinstance(old_val, bool):
            obj[last] = args.value.lower() in ("true", "1", "yes")
        elif isinstance(old_val, int):
            obj[last] = int(args.value)
        elif isinstance(old_val, float):
            obj[last] = float(args.value)
        else:
            obj[last] = args.value
    except ValueError:
        err(f"{args.key} expects a {type(old_val).__name__}, got: {args.value!r}")
        return 1

    # Rebuild config
    config = _dict_to_config(d)
    save_config(config, state_dir)
    ok(f"{args.key} = {obj[last]}")
    return 0


def run_reset(args: argparse.Namespace) -> int:
    """Execute the reset subcommand."""
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    save_config(Config(), state_dir)
    ok("Config reset to defaults")
    return 0


def _print_nested(d: Any, prefix: Any="") -> None:
    """Print a nested dict as indented key-value pairs."""
    for k, v in d.items():
        key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
        if isinstance(v, dict):
            _print_nested(v, key)
        else:
            print(f"  {key} = {v}")


def _dict_to_config(d: dict[str, Any]) -> Config:
    """Convert a nested dict to a flat config object."""
    from aictl.core.config import EngineEndpoints, SLOConfig, DaemonConfig
    return Config(
        engines=EngineEndpoints(**d.get("engines", {})),
        slo=SLOConfig(**{k: v for k, v in d.get("slo", {}).items() if k in SLOConfig.__dataclass_fields__}),
        daemon=DaemonConfig(**d.get("daemon", {})),
        trust_policy=d.get("trust_policy", "warn"),
        quadlet_rootless=d.get("quadlet_rootless", True),
        default_recipe=d.get("default_recipe", "local-chat"),
        model_cache_dir=d.get("model_cache_dir", ""),
        log_level=d.get("log_level", "info"),
    )

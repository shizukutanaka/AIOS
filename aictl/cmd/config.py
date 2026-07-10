"""aictl config — view and modify persistent configuration."""

from __future__ import annotations

from typing import Any

import argparse

from pathlib import Path

from aictl.core.output import ok, err, warn, print_json
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

    validate = csub.add_parser("validate", help="Validate config for common errors")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=run_validate)

    diff = csub.add_parser("diff", help="Show config keys that differ from defaults")
    diff.add_argument("--json", action="store_true")
    diff.set_defaults(func=run_diff)

    export_p = csub.add_parser("export", help="Export config to a portable JSON file")
    export_p.add_argument("--output", default="", help="Output file path (default: aios-config.json)")
    export_p.set_defaults(func=run_export)

    import_p = csub.add_parser("import", help="Import config from a JSON file")
    import_p.add_argument("file", help="Path to config JSON file")
    import_p.set_defaults(func=run_import)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def _redact_secrets(d: dict[str, Any]) -> dict[str, Any]:
    """Mask secret fields before a bulk config dump (show/diff).

    `fallback.api_key` is a cloud-provider credential. A bulk dump is exactly
    the kind of routine "let me check my config" output that ends up in
    terminal scrollback, a screen recording, or a pasted support-ticket
    transcript — so it must never appear in plaintext here. `config get
    fallback.api_key` (an explicit, single-key request) is intentionally left
    unredacted, matching the `aws configure get` convention.
    """
    fb = d.get("fallback")
    if isinstance(fb, dict) and fb.get("api_key"):
        d = {**d, "fallback": {**fb, "api_key": "***REDACTED***"}}
    return d


def run_show(args: argparse.Namespace) -> int:
    """Execute the show subcommand."""
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    config = load_config(state_dir)
    from dataclasses import asdict
    d = _redact_secrets(asdict(config))
    if getattr(args, "json", False):
        print_json(d)
        return 0

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


def _parse_bool(value: str) -> bool:
    """Parse a boolean config value with strtobool semantics.

    The naive `value.lower() in ("true", "1", "yes")` silently maps *any*
    unrecognized string (a typo like "treu", or "maybe", or "") to False — the
    `bool("false")`-class pitfall — so a misspelled "true" silently stores the
    opposite with no error. Recognize explicit true/false sets and reject
    everything else (raises ValueError, handled like the int/float branches).
    distutils.strtobool is gone in 3.12+ and we are stdlib-only, so inline it.
    """
    v = value.strip().lower()
    if v in ("y", "yes", "t", "true", "on", "1"):
        return True
    if v in ("n", "no", "f", "false", "off", "0"):
        return False
    raise ValueError(f"invalid boolean: {value!r}")


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
            obj[last] = _parse_bool(args.value)
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

    from aictl.core.hooks import on_config_changed
    on_config_changed(args.key, old_value=str(old_val), new_value=str(obj[last]),
                      state_dir=state_dir)

    if getattr(args, "json", False):
        print_json({"key": args.key, "value": obj[last]})
        return 0
    ok(f"{args.key} = {obj[last]}")
    return 0


def run_reset(args: argparse.Namespace) -> int:
    """Execute the reset subcommand."""
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    save_config(Config(), state_dir)
    if getattr(args, "json", False):
        print_json({"reset": True})
        return 0
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


def _validate_config(config: Any) -> list[str]:
    """Return a list of validation problems for the config (empty = valid)."""
    from dataclasses import asdict
    problems: list[str] = []
    d = asdict(config)

    # trust_policy must be one of the valid values
    valid_policies = {"enforce", "warn", "disabled"}
    if d.get("trust_policy", "warn") not in valid_policies:
        problems.append(f"trust_policy must be one of {sorted(valid_policies)}, "
                        f"got {d['trust_policy']!r}")

    # guard_policy must be one of the valid values
    valid_guard_policies = {"enforce", "warn", "off"}
    if d.get("guard_policy", "off") not in valid_guard_policies:
        problems.append(f"guard_policy must be one of {sorted(valid_guard_policies)}, "
                        f"got {d['guard_policy']!r}")

    # cache_similarity_floor must be a valid cosine-similarity bound: 0 (or
    # below) would match everything indiscriminately, and cosine similarity
    # for these embeddings never exceeds 1.0.
    floor = d.get("cache_similarity_floor", 0.92)
    if not (0.0 < floor <= 1.0):
        problems.append(f"cache_similarity_floor must be in (0.0, 1.0], got {floor!r}")

    # guard_model_check_endpoint, if set, must be http/https -- urllib's
    # default opener also handles file://, which would let a misconfigured
    # "endpoint" read arbitrary local files as part of the request attempt.
    endpoint = d.get("guard_model_check_endpoint", "")
    if endpoint:
        from urllib.parse import urlparse
        if urlparse(endpoint).scheme not in ("http", "https"):
            problems.append("guard_model_check_endpoint must be http:// or "
                            f"https://, got {endpoint!r}")

    # log_level must be valid
    valid_levels = {"debug", "info", "warning", "error", "critical"}
    if d.get("log_level", "info").lower() not in valid_levels:
        problems.append(f"log_level must be one of {sorted(valid_levels)}, "
                        f"got {d['log_level']!r}")

    # daemon port must be in valid range
    daemon_port = d.get("daemon", {}).get("port", 7700)
    if not (1 <= daemon_port <= 65535):
        problems.append(f"daemon.port {daemon_port} is out of range (1-65535)")

    # SLO values must be positive
    slo = d.get("slo", {})
    for field_name, label in [
        ("ttft_p95_ms", "slo.ttft_p95_ms"),
        ("itl_p95_ms", "slo.itl_p95_ms"),
        ("tokens_per_sec_min", "slo.tokens_per_sec_min"),
    ]:
        val = slo.get(field_name, 1)
        if val <= 0:
            problems.append(f"{label} must be > 0, got {val}")

    for field_name, label in [
        ("error_rate_max", "slo.error_rate_max"),
        ("kv_cache_max", "slo.kv_cache_max"),
    ]:
        val = slo.get(field_name, 0.5)
        if not (0.0 < val <= 1.0):
            problems.append(f"{label} must be in (0, 1], got {val}")

    # model_cache_dir must be writable if set
    cache_dir = d.get("model_cache_dir", "")
    if cache_dir:
        import os
        if not os.path.isdir(cache_dir):
            problems.append(f"model_cache_dir {cache_dir!r} does not exist")
        elif not os.access(cache_dir, os.W_OK):
            problems.append(f"model_cache_dir {cache_dir!r} is not writable")

    # engine endpoints must look like URLs
    for engine_name, url in d.get("engines", {}).items():
        if url and not (url.startswith("http://") or url.startswith("https://")):
            problems.append(f"engines.{engine_name} must be an http(s) URL, got {url!r}")

    return problems


def run_validate(args: argparse.Namespace) -> int:
    """Validate configuration for common errors."""
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    config = load_config(state_dir)
    problems = _validate_config(config)
    valid = not problems

    if getattr(args, "json", False):
        print_json({"valid": valid, "problems": problems})
        return 0 if valid else 1

    if valid:
        ok("Config is valid")
    else:
        err(f"Config has {len(problems)} problem(s):")
        for p in problems:
            print(f"    - {p}")
    return 0 if valid else 1


def _flatten_dict(d: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten a nested dict to dot-separated keys."""
    result: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            result.update(_flatten_dict(v, key))
        else:
            result[key] = v
    return result


def run_diff(args: argparse.Namespace) -> int:
    """Show config keys that differ from their default values."""
    from dataclasses import asdict
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    current = _flatten_dict(asdict(load_config(state_dir)))
    defaults = _flatten_dict(asdict(Config()))

    diffs = [
        {"key": k,
         "current": "***REDACTED***" if k == "fallback.api_key" and current.get(k) else current.get(k),
         "default": defaults.get(k)}
        for k in current
        if current.get(k) != defaults.get(k)
    ]

    if getattr(args, "json", False):
        print_json(diffs)
        return 0

    if not diffs:
        print("Config matches defaults (no customizations).")
        return 0

    print(f"  {len(diffs)} key(s) differ from defaults:")
    for d in diffs:
        print(f"    {d['key']}")
        print(f"      current : {d['current']}")
        print(f"      default : {d['default']}")
    return 0


def run_export(args: argparse.Namespace) -> int:
    """Export current config to a portable JSON file."""
    import json as _json
    from dataclasses import asdict
    from aictl.core.atomicio import atomic_write_text
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    config = load_config(state_dir)
    data = asdict(config)
    has_secret = bool(data.get("fallback", {}).get("api_key"))
    output = getattr(args, "output", "") or "aios-config.json"
    try:
        # Unredacted (unlike show/diff): this file feeds `config import` on
        # another machine, so stripping the secret would silently break that
        # round-trip. Written atomically with 0o600 instead — the same
        # protection api_keys.json got in Pass 157 — since this arbitrary,
        # user-chosen output path can carry the same cloud-provider credential.
        atomic_write_text(Path(output), _json.dumps(data, indent=2), mode=0o600)
    except OSError as exc:
        err(f"Failed to write: {exc}")
        return 1
    if getattr(args, "json", False):
        print_json({"exported": True, "output": output, "contains_secret": has_secret})
        return 0
    ok(f"Config exported: {output}")
    if has_secret:
        warn(f"{output} contains your fallback.api_key in plaintext — "
             f"handle it like a credential (don't commit it, don't paste it "
             f"into a support ticket).")
    return 0


def run_import(args: argparse.Namespace) -> int:
    """Import config from a JSON file."""
    import json as _json
    f = Path(args.file)
    if not f.exists():
        err(f"File not found: {args.file}")
        return 1
    try:
        data = _json.loads(f.read_text())
    except (ValueError, OSError) as exc:
        err(f"Invalid JSON: {exc}")
        return 1
    if not isinstance(data, dict):
        err("Config file must be a JSON object")
        return 1
    try:
        config = _dict_to_config(data)
    except (TypeError, KeyError) as exc:
        err(f"Config structure invalid: {exc}")
        return 1
    # Validate before persisting — importing a config with an out-of-range port,
    # an unknown trust_policy, a bad log_level, etc. would otherwise silently
    # break the daemon / trust enforcement on the next run.
    problems = _validate_config(config)
    if problems:
        if getattr(args, "json", False):
            print_json({"imported": False, "file": args.file, "problems": problems})
        else:
            err(f"Config not imported — {len(problems)} problem(s):")
            for p in problems:
                print(f"    - {p}")
        return 1
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else None
    save_config(config, state_dir)
    if getattr(args, "json", False):
        print_json({"imported": True, "file": args.file})
        return 0
    ok(f"Config imported from: {args.file}")
    return 0


def _dict_to_config(d: dict[str, Any]) -> Config:
    """Convert a nested dict to a flat config object."""
    from aictl.core.config import EngineEndpoints, SLOConfig, DaemonConfig, FallbackSettings
    return Config(
        engines=EngineEndpoints(**d.get("engines", {})),
        slo=SLOConfig(**{k: v for k, v in d.get("slo", {}).items() if k in SLOConfig.__dataclass_fields__}),
        daemon=DaemonConfig(**d.get("daemon", {})),
        fallback=FallbackSettings(**{k: v for k, v in d.get("fallback", {}).items()
                                     if k in FallbackSettings.__dataclass_fields__}),
        trust_policy=d.get("trust_policy", "warn"),
        guard_policy=d.get("guard_policy", "off"),
        guard_redact_output=d.get("guard_redact_output", False),
        guard_model_check_endpoint=d.get("guard_model_check_endpoint", ""),
        guard_model_check_model=d.get("guard_model_check_model", "llama-guard3"),
        cache_similarity_floor=d.get("cache_similarity_floor", 0.92),
        quadlet_rootless=d.get("quadlet_rootless", True),
        default_recipe=d.get("default_recipe", "local-chat"),
        model_cache_dir=d.get("model_cache_dir", ""),
        log_level=d.get("log_level", "info"),
    )

"""aictl plugin — manage CLI plugins."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.output import ok, err, warn, print_json, print_table


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("plugin", help="Manage CLI plugins")
    psub = p.add_subparsers(dest="plugin_cmd")

    ls = psub.add_parser("list", help="List discovered plugins")
    ls.add_argument("--json", action="store_true", help="JSON output")
    ls.set_defaults(func=run_list)

    reload_p = psub.add_parser("reload", help="Re-discover and reload plugins")
    reload_p.add_argument("--json", action="store_true", help="JSON output")
    reload_p.set_defaults(func=run_reload)

    info_p = psub.add_parser("info", help="Show details for a plugin")
    info_p.add_argument("name", help="Plugin name")
    info_p.add_argument("--json", action="store_true", help="JSON output")
    info_p.set_defaults(func=run_info)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def run_list(args: argparse.Namespace) -> int:
    """List all discovered plugins."""
    from aictl.core.plugins import discover_plugins, load_plugin

    plugins = discover_plugins()
    enriched = []
    for info in plugins:
        mod = load_plugin(info["path"])
        enriched.append({
            "name": info["name"],
            "path": info["path"],
            "dir": info["dir"],
            "has_register": bool(mod and hasattr(mod, "register")),
            "has_on_event": bool(mod and hasattr(mod, "on_event")),
            "loaded": mod is not None,
        })

    if getattr(args, "json", False):
        print_json(enriched)
        return 0

    if not enriched:
        print("No plugins found. Place .py files in ~/.aios/plugins/ or /opt/aios/plugins/")
        return 0

    print_table(enriched, ["name", "loaded", "has_register", "has_on_event", "dir"])
    return 0


def run_reload(args: argparse.Namespace) -> int:
    """Re-discover plugins and report which loaded successfully."""
    from aictl.core.plugins import discover_plugins, load_plugin
    import importlib
    import sys

    plugins = discover_plugins()
    results = []
    for info in plugins:
        name = info["name"]
        # Evict from sys.modules so the file is re-read on load_plugin()
        mod_key = f"_aios_plugin_{name}"
        sys.modules.pop(mod_key, None)

        mod = load_plugin(info["path"])
        status = "ok" if mod is not None else "error"
        results.append({"name": name, "status": status, "path": info["path"]})

    if getattr(args, "json", False):
        print_json(results)
        return 0

    if not results:
        ok("No plugins found")
        return 0

    loaded = sum(1 for r in results if r["status"] == "ok")
    ok(f"Reloaded {loaded}/{len(results)} plugin(s)")
    for r in results:
        icon = "✓" if r["status"] == "ok" else "✗"
        print(f"  {icon} {r['name']}")
    return 0


def run_info(args: argparse.Namespace) -> int:
    """Show details for a named plugin."""
    from aictl.core.plugins import discover_plugins, load_plugin

    plugins = discover_plugins()
    match = next((p for p in plugins if p["name"] == args.name), None)
    if not match:
        err(f"Plugin '{args.name}' not found")
        return 1

    mod = load_plugin(match["path"])
    doc = getattr(mod, "__doc__", "") or "" if mod else ""
    commands: list[str] = []
    if mod and hasattr(mod, "register"):
        # Introspect by calling register with a mock subparser
        import argparse as _ap
        _p = _ap.ArgumentParser()
        _s = _p.add_subparsers()
        try:
            mod.register(_s)
            commands = [a.dest for a in _s._group_actions] if hasattr(_s, "_group_actions") else []
        except Exception:
            pass

    data = {
        "name": match["name"],
        "path": match["path"],
        "dir": match["dir"],
        "loaded": mod is not None,
        "doc": doc.strip(),
        "commands": commands,
    }

    if getattr(args, "json", False):
        print_json(data)
        return 0

    ok(f"Plugin: {data['name']}")
    print(f"  Path:    {data['path']}")
    print(f"  Loaded:  {data['loaded']}")
    if data["doc"]:
        print(f"  Doc:     {data['doc']}")
    return 0

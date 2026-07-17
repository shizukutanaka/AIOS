"""Plugin system: extend aictl with custom commands and hooks.

Plugins are Python files in ~/.aios/plugins/ or /opt/aios/plugins/.
Each plugin exports a `register(sub)` function for adding CLI commands,
and optionally an `on_event(event)` function for event hooks.

Example plugin (~/.aios/plugins/my_plugin.py):

    def register(sub):
        p = sub.add_parser("my-command", help="My custom command")
        p.set_defaults(func=run)

    def run(args):
        print("Hello from my plugin!")
        return 0

    def on_event(event):
        if event.type == "stack.applied":
            print(f"Stack {event.data.get('name')} applied!")
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

from aictl.core.state import DEFAULT_STATE_DIR

logger = logging.getLogger("aios.plugins")

PLUGIN_DIRS = [
    DEFAULT_STATE_DIR / "plugins",
    Path("/opt/aios/plugins"),
]


def discover_plugins() -> list[dict[str, Any]]:
    """Discover available plugins from plugin directories."""
    plugins: list[dict[str, Any]] = []
    seen: set[str] = set()

    for plugin_dir in PLUGIN_DIRS:
        if not plugin_dir.is_dir():
            continue

        for path in sorted(plugin_dir.glob("*.py")):
            name = path.stem
            if name.startswith("_") or name in seen:
                continue
            seen.add(name)

            plugins.append({
                "name": name,
                "path": str(path),
                "dir": str(plugin_dir),
            })

    return plugins


def load_plugin(path: str) -> Any:
    """Load a plugin module from a file path."""
    p = Path(path)
    spec = importlib.util.spec_from_file_location(p.stem, str(p))
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        return module
    except Exception as e:
        logger.warning("Failed to load plugin %s: %s", p.stem, e)
        return None


def register_plugins(sub: Any) -> int:
    """Discover and register all plugin commands. Returns count."""
    count = 0
    for info in discover_plugins():
        module = load_plugin(info["path"])
        if module and hasattr(module, "register"):
            try:
                module.register(sub)
                count += 1
            except Exception as e:
                logger.warning("Plugin %s register failed: %s", info["name"], e)
    return count


def wire_plugin_events() -> int:
    """Wire plugin event handlers into the event bus. Returns count."""
    from aictl.core.events import get_bus

    bus = get_bus()
    count = 0

    for info in discover_plugins():
        module = load_plugin(info["path"])
        if module and hasattr(module, "on_event"):
            try:
                bus.subscribe_all(module.on_event)
                count += 1
            except Exception as e:
                logger.warning("Plugin %s event wiring failed: %s", info["name"], e)

    return count

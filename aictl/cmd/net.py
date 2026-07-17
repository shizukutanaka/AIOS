"""aictl net — network diagnostics for AI services."""

from __future__ import annotations

from typing import Any

import argparse

import socket
import time
import urllib.request

from aictl.core.config import load_config
from aictl.core.state import StateStore


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("net", help="Network diagnostics")
    # SUPPRESS default so the local flag never clobbers a global `aictl --json`.
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                   help="Emit results as JSON")
    p.set_defaults(func=run)


def collect_diagnostics(args: argparse.Namespace) -> dict[str, Any]:
    """Probe engine endpoints, daemon, proxy and DNS. Returns a results dict."""
    store = StateStore(getattr(args, "state_dir", None))
    config = load_config(store.dir)
    endpoints = config.engines.to_dict()

    engines = []
    for name, url in endpoints.items():
        host, port = _parse_endpoint(url)
        reachable, latency = _check_tcp(host, port)
        engines.append({"name": name, "url": url, "reachable": reachable,
                        "latency_ms": round(latency, 1) if reachable else None})

    daemon_url = f"http://{config.daemon.host}:{config.daemon.port}"
    d_ok, d_lat = _check_http(f"{daemon_url}/v1/health")
    daemon = {"url": daemon_url, "reachable": d_ok,
              "latency_ms": round(d_lat, 1) if d_ok else None}

    from aictl.core.constants import PROXY_PORT
    proxy_url = f"http://127.0.0.1:{PROXY_PORT}"
    p_ok, p_lat = _check_http(f"{proxy_url}/health")
    proxy = {"url": proxy_url, "reachable": p_ok,
             "latency_ms": round(p_lat, 1) if p_ok else None}

    dns = []
    for host in ["ghcr.io", "registry.ollama.ai", "huggingface.co"]:
        t0 = time.monotonic()
        try:
            socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
            dns.append({"host": host, "resolved": True,
                        "latency_ms": round((time.monotonic() - t0) * 1000, 1)})
        except socket.gaierror:
            dns.append({"host": host, "resolved": False, "latency_ms": None})

    return {"engines": engines, "daemon": daemon, "proxy": proxy, "dns": dns}


def run(args: argparse.Namespace) -> int:
    """Execute the net command."""
    result = collect_diagnostics(args)

    if getattr(args, "json", False):
        from aictl.core.output import print_json
        print_json(result)
        return 0

    print("Network diagnostics")
    print()
    for e in result["engines"]:
        icon = "\u2713" if e["reachable"] else "\u2717"
        lat = f"{e['latency_ms']:.0f}ms" if e["reachable"] else "unreachable"
        print(f"  {icon} {e['name']:10s} {e['url']:35s} {lat}")

    print()
    for label, info, down in (("aiosd", result["daemon"], "not running"),
                              ("proxy", result["proxy"], "not running")):
        icon = "\u2713" if info["reachable"] else "\u2717"
        lat = f"{info['latency_ms']:.0f}ms" if info["reachable"] else down
        print(f"  {icon} {label:10s} {info['url']:35s} {lat}")

    print()
    for d in result["dns"]:
        if d["resolved"]:
            print(f"  \u2713 DNS  {d['host']:35s} {d['latency_ms']:.0f}ms")
        else:
            print(f"  \u2717 DNS  {d['host']:35s} failed")

    return 0


def _parse_endpoint(url: str) -> tuple[str, int]:
    """Parse the raw input into a structured form."""
    url = url.replace("http://", "").replace("https://", "")
    if ":" in url:
        host, port_str = url.split(":", 1)
        port_str = port_str.split("/")[0]
        return host, int(port_str)
    return url, 80


def _check_tcp(host: str, port: int, timeout: int = 3) -> tuple[bool, float]:
    """Validate or inspect the given state."""
    t0 = time.monotonic()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True, (time.monotonic() - t0) * 1000
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False, 0


def _check_http(url: str, timeout: int = 3) -> tuple[bool, float]:
    """Validate or inspect the given state."""
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            resp.read()
            return True, (time.monotonic() - t0) * 1000
    except Exception:
        return False, 0

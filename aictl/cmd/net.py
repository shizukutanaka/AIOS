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
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the net command."""
    store = StateStore(getattr(args, "state_dir", None))
    config = load_config(store.dir)
    endpoints = config.engines.to_dict()

    print("Network diagnostics")
    print()

    # Check each engine endpoint
    for name, url in endpoints.items():
        host, port = _parse_endpoint(url)
        reachable, latency = _check_tcp(host, port)
        icon = "\u2713" if reachable else "\u2717"
        lat = f"{latency:.0f}ms" if reachable else "unreachable"
        print(f"  {icon} {name:10s} {url:35s} {lat}")

    # Check daemon
    print()
    daemon_url = f"http://{config.daemon.host}:{config.daemon.port}"
    reachable, latency = _check_http(f"{daemon_url}/v1/health")
    icon = "\u2713" if reachable else "\u2717"
    lat = f"{latency:.0f}ms" if reachable else "not running"
    print(f"  {icon} {'aiosd':10s} {daemon_url:35s} {lat}")

    # Check proxy
    from aictl.core.constants import PROXY_PORT
    proxy_url = f"http://127.0.0.1:{PROXY_PORT}"
    reachable, latency = _check_http(f"{proxy_url}/health")
    icon = "\u2713" if reachable else "\u2717"
    lat = f"{latency:.0f}ms" if reachable else "not running"
    print(f"  {icon} {'proxy':10s} {proxy_url:35s} {lat}")

    # DNS
    print()
    for host in ["ghcr.io", "registry.ollama.ai", "huggingface.co"]:
        t0 = time.monotonic()
        try:
            socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
            latency = (time.monotonic() - t0) * 1000
            print(f"  \u2713 DNS  {host:35s} {latency:.0f}ms")
        except socket.gaierror:
            print(f"  \u2717 DNS  {host:35s} failed")

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

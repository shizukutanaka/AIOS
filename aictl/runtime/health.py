"""Health monitor: watch running services and auto-restart on failure.

Runs as part of the SLO Governor daemon. Checks:
  - Container health status (via podman/docker inspect)
  - HTTP health endpoints
  - Process presence
"""

from __future__ import annotations

import subprocess
import time
import urllib.request
from dataclasses import dataclass

from aictl.runtime.broker import detect_container_runtime


@dataclass
class ServiceHealth:
    name: str
    healthy: bool = False
    status: str = "unknown"
    last_check: float = 0.0
    consecutive_failures: int = 0
    error: str = ""


def check_container_health(container_name: str) -> ServiceHealth:
    """Check health of a container via podman/docker inspect."""
    rt = detect_container_runtime()
    if rt == "none":
        return ServiceHealth(name=container_name, status="no-runtime")

    sh = ServiceHealth(name=container_name, last_check=time.time())

    try:
        result = subprocess.run(
            [rt, "inspect", "--format", "{{.State.Status}}:{{.State.Health.Status}}",
             container_name],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            sh.status = "not-found"
            sh.error = result.stderr.strip()[:100]
            return sh

        parts = result.stdout.strip().split(":")
        container_status = parts[0] if parts else "unknown"
        health_status = parts[1] if len(parts) > 1 else ""

        if container_status == "running":
            if health_status in ("healthy", ""):
                sh.healthy = True
                sh.status = "healthy"
            elif health_status == "unhealthy":
                sh.status = "unhealthy"
            elif health_status == "starting":
                sh.healthy = True
                sh.status = "starting"
            else:
                sh.healthy = False
                sh.status = container_status
        else:
            sh.status = container_status

    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        sh.status = "error"
        sh.error = str(e)[:100]

    return sh


def check_http_health(endpoint: str, path: str = "/health", timeout: int = 5) -> ServiceHealth:
    """Check health of a service via HTTP endpoint."""
    url = f"{endpoint.rstrip('/')}{path}"
    sh = ServiceHealth(name=url, last_check=time.time())

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status == 200:
                sh.healthy = True
                sh.status = "healthy"
            else:
                sh.status = f"http-{resp.status}"
    except Exception as e:
        sh.status = "unreachable"
        sh.error = str(e)[:100]

    return sh


def restart_container(container_name: str) -> bool:
    """Restart a container."""
    rt = detect_container_runtime()
    if rt == "none":
        return False

    try:
        result = subprocess.run(
            [rt, "restart", container_name],
            capture_output=True, timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def check_all_aios_services() -> list[ServiceHealth]:
    """Check health of all aios-managed containers."""
    rt = detect_container_runtime()
    if rt == "none":
        return []

    results: list[ServiceHealth] = []
    try:
        proc = subprocess.run(
            [rt, "ps", "-a", "--filter", "name=aios-", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
        for name in proc.stdout.strip().splitlines():
            if name:
                sh = check_container_health(name)
                results.append(sh)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # best-effort; failure is non-critical

    return results

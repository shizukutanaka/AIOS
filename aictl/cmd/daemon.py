"""aictl daemon — manage the aiosd background daemon."""

from __future__ import annotations

from typing import Any

import argparse

from aictl.core.constants import DAEMON_HOST, DAEMON_PORT
from aictl.core.output import ok, err, print_json
from aictl.core.state import DEFAULT_STATE_DIR


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("daemon", help="Manage the aiosd background daemon")
    dsub = p.add_subparsers(dest="daemon_cmd")

    status = dsub.add_parser("status", help="Show daemon status and health")
    status.add_argument("--host", default=DAEMON_HOST)
    status.add_argument("--port", type=int, default=DAEMON_PORT)
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=run_status)

    stop = dsub.add_parser("stop", help="Stop the running daemon process")
    stop.add_argument("--host", default=DAEMON_HOST)
    stop.add_argument("--port", type=int, default=DAEMON_PORT)
    stop.set_defaults(func=run_stop)

    restart = dsub.add_parser("restart", help="Restart the daemon process")
    restart.add_argument("--host", default=DAEMON_HOST)
    restart.add_argument("--port", type=int, default=DAEMON_PORT)
    restart.set_defaults(func=run_restart)

    logs = dsub.add_parser("logs", help="Show recent daemon log entries")
    logs.add_argument("-n", "--lines", type=int, default=50)
    logs.add_argument("--json", action="store_true")
    logs.set_defaults(func=run_logs)

    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def _query_health(host: str, port: int) -> dict[str, Any] | None:
    """Query /v1/health; return dict on success, None on error."""
    import urllib.request
    import json
    url = f"http://{host}:{port}/v1/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _find_daemon_pid(port: int) -> int | None:
    """Find the PID of the process listening on the given port via /proc."""
    import os
    import socket
    try:
        # encode port as hex for /proc/net/tcp
        port_hex = f"{port:04X}"
        for proto in ("tcp", "tcp6"):
            proc_net = f"/proc/net/{proto}"
            if not os.path.exists(proc_net):
                continue
            with open(proc_net) as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 4:
                        continue
                    local = parts[1]
                    if ":" not in local:
                        continue
                    lport = local.split(":")[1]
                    if lport.upper() == port_hex and parts[3] == "0A":  # LISTEN
                        inode = parts[9]
                        # find PID owning this socket inode
                        for pid in os.listdir("/proc"):
                            if not pid.isdigit():
                                continue
                            fd_dir = f"/proc/{pid}/fd"
                            try:
                                for fd in os.listdir(fd_dir):
                                    target = os.readlink(f"{fd_dir}/{fd}")
                                    if f"socket:[{inode}]" == target:
                                        return int(pid)
                            except (OSError, PermissionError):
                                continue
    except Exception:
        pass
    return None


def run_status(args: argparse.Namespace) -> int:
    """Query daemon health endpoint."""
    host = getattr(args, "host", DAEMON_HOST)
    port = getattr(args, "port", DAEMON_PORT)
    health = _query_health(host, port)

    if health is None:
        result = {"running": False, "host": host, "port": port}
        if getattr(args, "json", False):
            print_json(result)
        else:
            err(f"Daemon not reachable at {host}:{port}")
        return 1

    pid = _find_daemon_pid(port)
    result = {
        "running": True,
        "host": host,
        "port": port,
        "pid": pid,
        **health,
    }

    if getattr(args, "json", False):
        print_json(result)
        return 0

    status = health.get("status", "unknown")
    uptime = health.get("uptime_seconds", 0)
    profile = health.get("profile", "")
    ok(f"Daemon {status} on {host}:{port}" + (f" (pid={pid})" if pid else ""))
    print(f"  uptime  : {uptime:.0f}s")
    if profile:
        print(f"  profile : {profile}")
    return 0


def run_stop(args: argparse.Namespace) -> int:
    """Stop the running daemon via SIGTERM."""
    import signal
    host = getattr(args, "host", DAEMON_HOST)
    port = getattr(args, "port", DAEMON_PORT)
    pid = _find_daemon_pid(port)
    if pid is None:
        err(f"No daemon process found on port {port}")
        return 1
    try:
        import os
        os.kill(pid, signal.SIGTERM)
        ok(f"Sent SIGTERM to daemon (pid={pid})")
        return 0
    except OSError as e:
        err(f"Cannot stop daemon (pid={pid}): {e}")
        return 1


def run_restart(args: argparse.Namespace) -> int:
    """Restart by stopping the current daemon; run 'aictl serve' to start a new one."""
    ret = run_stop(args)
    if ret != 0:
        return ret
    print("Daemon stopped. Run 'aictl serve' to start a new instance.")
    return 0


def run_logs(args: argparse.Namespace) -> int:
    """Show recent daemon log entries from the audit log."""
    from pathlib import Path
    from aictl.core.output import warn

    n = getattr(args, "lines", 50)
    log_path = DEFAULT_STATE_DIR / "daemon.log"
    if not log_path.exists():
        warn(f"No daemon log found at {log_path}")
        if getattr(args, "json", False):
            print_json({"lines": [], "path": str(log_path)})
        return 0

    lines = log_path.read_text().splitlines()[-n:]

    if getattr(args, "json", False):
        print_json({"lines": lines, "path": str(log_path)})
        return 0

    for line in lines:
        print(line)
    return 0

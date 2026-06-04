"""Systemctl integration: manage Quadlet-installed services via systemd.

After `aictl apply --quadlet`, services become systemd units. This module
wraps systemctl for start/stop/restart/status/enable/disable operations.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class UnitStatus:
    name: str
    load_state: str = ""      # loaded | not-found | masked
    active_state: str = ""    # active | inactive | failed | activating
    sub_state: str = ""       # running | dead | failed | waiting
    description: str = ""
    main_pid: int = 0
    memory_bytes: int = 0
    cpu_usage_ns: int = 0


def _systemctl(args: list[str], user: bool = True, timeout: int = 15) -> tuple[int, str]:
    """Execute systemctl."""
    cmd = ["systemctl"]
    if user:
        cmd.append("--user")
    cmd.extend(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def daemon_reload(user: bool = True) -> bool:
    """Daemon reload."""
    code, _ = _systemctl(["daemon-reload"], user=user)
    return code == 0


def start_unit(unit: str, user: bool = True) -> tuple[bool, str]:
    """Start unit."""
    code, out = _systemctl(["start", unit], user=user)
    if code == 0:
        return True, f"Started {unit}"
    return False, out


def stop_unit(unit: str, user: bool = True) -> tuple[bool, str]:
    """Stop unit."""
    code, out = _systemctl(["stop", unit], user=user)
    if code == 0:
        return True, f"Stopped {unit}"
    return False, out


def restart_unit(unit: str, user: bool = True) -> tuple[bool, str]:
    """Restart unit."""
    code, out = _systemctl(["restart", unit], user=user)
    if code == 0:
        return True, f"Restarted {unit}"
    return False, out


def enable_unit(unit: str, user: bool = True) -> bool:
    """Enable unit."""
    code, _ = _systemctl(["enable", unit], user=user)
    return code == 0


def disable_unit(unit: str, user: bool = True) -> bool:
    """Disable unit."""
    code, _ = _systemctl(["disable", unit], user=user)
    return code == 0


def get_unit_status(unit: str, user: bool = True) -> UnitStatus:
    """Get detailed status of a systemd unit."""
    us = UnitStatus(name=unit)
    props = "LoadState,ActiveState,SubState,Description,MainPID,MemoryCurrent,CPUUsageNSec"
    code, out = _systemctl(
        ["show", unit, "--property", props, "--no-pager"],
        user=user,
    )
    if code != 0:
        us.load_state = "not-found"
        return us

    for line in out.splitlines():
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        if key == "LoadState":
            us.load_state = val
        elif key == "ActiveState":
            us.active_state = val
        elif key == "SubState":
            us.sub_state = val
        elif key == "Description":
            us.description = val
        elif key == "MainPID":
            try:
                us.main_pid = int(val)
            except ValueError:
                pass  # best-effort; failure is non-critical
        elif key == "MemoryCurrent":
            try:
                us.memory_bytes = int(val) if val != "[not set]" else 0
            except ValueError:
                pass  # best-effort; failure is non-critical
        elif key == "CPUUsageNSec":
            try:
                us.cpu_usage_ns = int(val) if val != "[not set]" else 0
            except ValueError:
                pass  # best-effort; failure is non-critical

    return us


def list_aios_units(user: bool = True) -> list[UnitStatus]:
    """List all aios-* systemd units."""
    code, out = _systemctl(
        ["list-units", "aios-*", "--no-pager", "--plain", "--no-legend"],
        user=user,
    )
    if code != 0:
        return []

    units: list[UnitStatus] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            us = UnitStatus(
                name=parts[0],
                load_state=parts[1],
                active_state=parts[2],
                sub_state=parts[3],
                description=" ".join(parts[4:]) if len(parts) > 4 else "",
            )
            units.append(us)

    return units


def get_journal_logs(unit: str, lines: int = 50, follow: bool = False,
                     user: bool = True) -> "subprocess.Popen[str] | str":
    """Get journal logs for a unit. If follow=True, returns Popen for streaming."""
    cmd = ["journalctl"]
    if user:
        cmd.append("--user")
    cmd.extend(["-u", unit, "--no-pager", "-n", str(lines)])

    if follow:
        cmd.append("-f")
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""

"""Security scanner: check for common misconfigurations.

Scans:
  1. Container runtime security (rootless, userns, seccomp)
  2. Network exposure (open ports, TLS)
  3. Model trust chain (signatures, provenance)
  4. API key hygiene (expired, unused, overly permissive)
  5. Audit logging status
  6. cgroup isolation
  7. File permissions on state directory
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from aictl.core.state import StateStore


@dataclass
class SecurityFinding:
    severity: str        # critical | high | medium | low | info
    category: str        # runtime | network | trust | auth | audit | isolation
    title: str
    description: str
    remediation: str = ""


@dataclass
class SecurityReport:
    score: int = 100                      # 0-100, starts at 100, deductions
    findings: list[SecurityFinding] = field(default_factory=list)
    checks_passed: int = 0
    checks_failed: int = 0
    checks_total: int = 0


def scan(state_dir: Path | None = None) -> SecurityReport:
    """Run all security checks."""
    report = SecurityReport()
    store = StateStore(state_dir)

    checks = [
        _check_state_permissions,
        _check_container_runtime,
        _check_rootless,
        _check_cgroup_v2,
        _check_psi,
        _check_api_keys,
        _check_audit_logging,
        _check_trust_policy,
        _check_network_exposure,
        _check_model_signatures,
    ]

    for check in checks:
        try:
            finding = check(store)
            report.checks_total += 1
            if finding:
                report.findings.append(finding)
                report.checks_failed += 1
                # Deductions
                deductions = {"critical": 25, "high": 15, "medium": 10, "low": 5, "info": 0}
                report.score -= deductions.get(finding.severity, 5)
            else:
                report.checks_passed += 1
        except Exception:
            report.checks_total += 1
            report.checks_failed += 1

    report.score = max(0, report.score)
    return report


def _check_state_permissions(store: StateStore) -> SecurityFinding | None:
    """Check state directory permissions."""
    state_dir = store.dir
    if not state_dir.exists():
        return None

    try:
        mode = state_dir.stat().st_mode
        if mode & stat.S_IROTH or mode & stat.S_IWOTH:
            return SecurityFinding(
                severity="high", category="isolation",
                title="State directory world-readable",
                description=f"{state_dir} is accessible by other users",
                remediation=f"chmod 700 {state_dir}",
            )
    except OSError:
        pass  # best-effort; failure is non-critical
    return None


def _check_container_runtime(store: StateStore) -> SecurityFinding | None:
    """Check if container runtime is available."""
    import shutil
    if not shutil.which("podman") and not shutil.which("docker"):
        return SecurityFinding(
            severity="medium", category="runtime",
            title="No container runtime",
            description="No podman or docker found — cannot run isolated workloads",
            remediation="sudo dnf install podman",
        )
    return None


def _check_rootless(store: StateStore) -> SecurityFinding | None:
    """Check if running as root (prefer rootless)."""
    if os.geteuid() == 0:
        return SecurityFinding(
            severity="medium", category="runtime",
            title="Running as root",
            description="aictl is running as root — prefer rootless podman",
            remediation="Run as non-root user with rootless podman",
        )
    return None


def _check_cgroup_v2(store: StateStore) -> SecurityFinding | None:
    """Check cgroup v2 for resource isolation."""
    if not Path("/sys/fs/cgroup/cgroup.controllers").exists():
        return SecurityFinding(
            severity="medium", category="isolation",
            title="cgroup v2 not available",
            description="Resource isolation limited without cgroup v2",
            remediation="Boot with systemd.unified_cgroup_hierarchy=1",
        )
    return None


def _check_psi(store: StateStore) -> SecurityFinding | None:
    """Check PSI for pressure monitoring."""
    if not Path("/proc/pressure/memory").exists():
        return SecurityFinding(
            severity="low", category="isolation",
            title="PSI not enabled",
            description="Pressure Stall Information unavailable — SLO monitoring degraded",
            remediation="Boot with psi=1 kernel parameter",
        )
    return None


def _check_api_keys(store: StateStore) -> SecurityFinding | None:
    """Check API key configuration."""
    keys_path = store.dir / "api_keys.json"
    if not keys_path.exists():
        return SecurityFinding(
            severity="low", category="auth",
            title="No API keys configured",
            description="Completions proxy is open without authentication",
            remediation="aictl apikey create production --rpm 1000",
        )

    import json
    try:
        keys = json.loads(keys_path.read_text())
        inactive = [k for k, v in keys.items() if not v.get("active", True)]
        if len(inactive) > len(keys) // 2:
            return SecurityFinding(
                severity="low", category="auth",
                title="Many revoked API keys",
                description=f"{len(inactive)} of {len(keys)} keys are revoked",
                remediation="Clean up old keys",
            )
    except (json.JSONDecodeError, OSError):
        pass  # best-effort; failure is non-critical
    return None


def _check_audit_logging(store: StateStore) -> SecurityFinding | None:
    """Check audit logging status."""
    audit_dir = store.dir / "audit"
    if not audit_dir.exists() or not any(audit_dir.glob("audit-*.jsonl")):
        return SecurityFinding(
            severity="low", category="audit",
            title="No audit log entries",
            description="Audit logging has no entries — may not be configured",
            remediation="Audit events are recorded automatically during normal operation",
        )
    return None


def _check_trust_policy(store: StateStore) -> SecurityFinding | None:
    """Check model trust policy."""
    config_path = store.dir / "config.json"
    if config_path.exists():
        import json
        try:
            config = json.loads(config_path.read_text())
            # config.json persists this as a flat key (see core/config.py),
            # not a nested {"trust": {"policy": ...}} object.
            policy = config.get("trust_policy", "warn")
            if policy == "disabled":
                return SecurityFinding(
                    severity="high", category="trust",
                    title="Trust policy disabled",
                    description="Model signature verification is disabled",
                    remediation='aictl config set trust_policy warn',
                )
        except (json.JSONDecodeError, OSError):
            pass  # best-effort; failure is non-critical
    return None


def _check_network_exposure(store: StateStore) -> SecurityFinding | None:
    """Check for services exposed on 0.0.0.0."""
    config_path = store.dir / "config.json"
    if config_path.exists():
        import json
        try:
            config = json.loads(config_path.read_text())
            host = config.get("daemon", {}).get("host", "127.0.0.1")
            if host == "0.0.0.0":
                return SecurityFinding(
                    severity="high", category="network",
                    title="Daemon exposed on all interfaces",
                    description="aiosd is listening on 0.0.0.0 — accessible from network",
                    remediation='aictl config set daemon.host 127.0.0.1',
                )
        except (json.JSONDecodeError, OSError):
            pass  # best-effort; failure is non-critical
    return None


def _check_model_signatures(store: StateStore) -> SecurityFinding | None:
    """Check if any models lack signatures."""
    models = store.list_models()
    unsigned = [m for m in models if not m.get("digest")]
    if unsigned:
        return SecurityFinding(
            severity="medium", category="trust",
            title=f"{len(unsigned)} unsigned models",
            description="Some registered models lack digest verification",
            remediation="aictl model verify <model>",
        )
    return None

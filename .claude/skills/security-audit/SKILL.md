---
name: security-audit
description: Run security audit and remediate findings
---
# Security Audit

## Quick scan
```bash
aictl security              # 10-check scan, 0-100 score
aictl doctor --deep          # Includes security in deep mode
```

## Checks (severity order)
1. **HIGH** — Trust policy disabled → `aictl config set trust.policy warn`
2. **HIGH** — Daemon on 0.0.0.0 → `aictl config set daemon.host 127.0.0.1`
3. **HIGH** — State dir world-readable → `chmod 700 ~/.aios`
4. **MEDIUM** — No container runtime → `sudo dnf install podman`
5. **MEDIUM** — Running as root → use rootless podman
6. **MEDIUM** — No cgroup v2 → `systemd.unified_cgroup_hierarchy=1`
7. **MEDIUM** — Unsigned models → `aictl model verify <model>`
8. **LOW** — No PSI → `psi=1` kernel param
9. **LOW** — No API keys → `aictl apikey create production --rpm 1000`
10. **LOW** — No audit entries → normal operation generates these

## Model supply chain
```bash
cosign sign ghcr.io/org/model@sha256:...        # Sign
aictl model verify ghcr.io/org/model:latest     # Verify
aictl config set trust.policy enforce            # Enforce
```

# ADR-002: Quadlet as primary local orchestration

## Status: Accepted

## Context
Local AI services need lifecycle management. Options: docker-compose, K8s, Podman Quadlet, plain systemd.

## Decision
Use Podman Quadlet (.container files) as primary local orchestration. K3s only for multi-node.

## Rationale
- Quadlet generates native systemd units — services survive reboot
- No daemon dependency (unlike Docker)
- Rootless by default
- Same manifest can convert to K8s when cluster grows
- Health checks, auto-update, resource limits via systemd

## Consequences
- Docker users need to learn Podman conventions
- Quadlet syntax differs from docker-compose

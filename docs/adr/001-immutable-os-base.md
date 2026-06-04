# ADR-001: bootc (Fedora 42) as immutable OS base

## Status: Accepted

## Context
AI inference OS needs transactional updates and rollback. Options: Fedora CoreOS, Talos, Bottlerocket, NixOS, bootc.

## Decision
Use bootc with Fedora 42 base image. Build OS from Containerfile, deliver as OCI image.

## Rationale
- bootc v1.15.0 is CNCF Sandbox, mature
- Same container tooling for OS and app delivery
- /etc merges on upgrade, /var persists — fits our state model
- NVIDIA drivers compile during build via akmods
- Podman + Quadlet work natively inside bootc

## Consequences
- Requires Fedora-based ecosystem knowledge
- NVIDIA Secure Boot requires kernel module signing or disabling

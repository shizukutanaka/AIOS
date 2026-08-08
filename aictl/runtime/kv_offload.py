"""vLLM KV prefix-cache offloading advice (OffloadingConnector).

`optimize_vllm_flags` has always emitted `--enable-prefix-caching`, but that
cache lives in whatever VRAM is left after the weights. On a large model /
small GPU the leftover is thin, so on prefix-heavy workloads — multi-turn
chat, RAG sharing one system prompt, agent loops replaying a transcript — the
cache thrashes and the reuse the flag promises never materializes.

vLLM's OffloadingConnector (2026) extends that cache into pinned host memory:
completed GPU blocks are DMA'd out to a CPU tier instead of being dropped, so
a prefix that no longer fits in VRAM is still a cache hit rather than a
recompute. This module decides whether that trade is worth making and sizes
the CPU tier.

Two properties drive the design:

* `cpu_bytes_to_use` is **pinned** memory — page-locked, unswappable, and
  gone from the OS for the engine's lifetime. Over-allocating it degrades the
  entire host, not just the engine, so sizing is deliberately conservative
  (see the KV_OFFLOAD_* constants) and the advisor refuses rather than
  guesses when host RAM is unknown.
* Offloading buys prefix *reuse*, not capacity for weights. It does nothing
  for a model that doesn't fit, and little for single-shot prompts with no
  shared prefix. Recommending it there would be cargo-cult tuning, so the
  advisor declines and says why.

Schema note: the emitted `--kv-transfer-config` uses only keys confirmed
against the connector's own pull request (kv_connector / kv_role /
kv_connector_extra_config.{block_size, cpu_bytes_to_use}). `spec_name` and
the multi-tier options appear in secondary sources but could not be verified
against a primary one from this environment, so they are deliberately not
emitted — consistent with the project's rule of shipping one verified
contract over two guessed ones.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from aictl.core.constants import (
    KV_OFFLOAD_BLOCK_SIZE,
    KV_OFFLOAD_HOST_RAM_FRACTION,
    KV_OFFLOAD_MIN_BYTES,
    KV_OFFLOAD_MIN_FREE_RAM_MB,
)

# Accelerators the connector supports. CPU-only hosts have no GPU tier to
# offload *from*, so the whole mechanism is meaningless there.
SUPPORTED_VENDORS = ("nvidia", "amd", "intel")


@dataclass
class OffloadAdvice:
    """Whether to enable KV offloading, how big to make it, and why."""
    recommended: bool
    cpu_bytes: int = 0
    reason: str = ""
    flag: str = ""                       # ready-to-paste --kv-transfer-config
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "recommended": self.recommended,
            "cpu_bytes": self.cpu_bytes,
            "cpu_gib": round(self.cpu_bytes / 1024**3, 1) if self.cpu_bytes else 0,
            "reason": self.reason,
            "flag": self.flag,
            "notes": self.notes,
        }


def build_kv_transfer_config(cpu_bytes: int,
                             block_size: int = KV_OFFLOAD_BLOCK_SIZE) -> str:
    """Render the --kv-transfer-config JSON value for the OffloadingConnector.

    Kept separate from the advisory logic so callers that have already decided
    to offload (and know their own sizing) can emit a correct flag without
    going through the heuristics.
    """
    return json.dumps({
        "kv_connector": "OffloadingConnector",
        "kv_role": "kv_both",
        "kv_connector_extra_config": {
            "block_size": block_size,
            "cpu_bytes_to_use": cpu_bytes,
        },
    }, separators=(",", ":"))


def size_cpu_tier(host_ram_mb: int, gpu_kv_mb: float = 0.0) -> int:
    """Pick a pinned-host-memory budget, in bytes. 0 means "don't offload".

    Conservative by construction: takes at most KV_OFFLOAD_HOST_RAM_FRACTION
    of host RAM, always leaves KV_OFFLOAD_MIN_FREE_RAM_MB behind, and returns
    0 rather than a token allocation when the result would be too small to
    pay for the connector's overhead.
    """
    if host_ram_mb <= 0:
        return 0
    budget_mb = min(
        host_ram_mb * KV_OFFLOAD_HOST_RAM_FRACTION,
        max(0.0, host_ram_mb - KV_OFFLOAD_MIN_FREE_RAM_MB),
    )
    # An offload tier smaller than the GPU tier it backs adds a hop without
    # meaningfully extending the cache.
    if gpu_kv_mb > 0 and budget_mb < gpu_kv_mb:
        return 0
    cpu_bytes = int(budget_mb * 1024**2)
    return cpu_bytes if cpu_bytes >= KV_OFFLOAD_MIN_BYTES else 0


def measured_prefix_reuse() -> float | None:
    """This process's observed prefix-cache reuse rate, or None if unmeasured.

    Reads the router's own lookup accounting rather than sampling anything:
    if requests have flowed through prefix-aware routing, the workload has
    already answered the question this module would otherwise guess at.

    Returns None (not 0.0) when nothing has been observed — see
    `PrefixRouteTracker.reuse_rate` for why that distinction matters.
    """
    try:
        from aictl.runtime.prefix_route import get_default_tracker
        return get_default_tracker().reuse_rate()
    except Exception:
        # Advisory path: never let measurement failure break flag generation.
        return None


def advise_kv_offload(
    *,
    host_ram_mb: int,
    gpu_kv_mb: float,
    vendor: str = "nvidia",
    prefix_reuse: float | None = None,
) -> OffloadAdvice:
    """Decide whether extending the prefix cache into host memory is worth it.

    `gpu_kv_mb` is the VRAM left for KV cache after weights — the same
    quantity `optimize_vllm_flags` computes when auto-sizing context.

    `prefix_reuse` is a measured hit rate in [0, 1]; when supplied it is
    believed over any heuristic, in both directions. **When omitted it is
    read from this process's prefix-aware router** (`measured_prefix_reuse`),
    so advice reflects traffic the process actually served — which means the
    same arguments can yield different advice in a process that has served
    requests versus a fresh one. That is the intent: an observed workload
    beats an assumed one. Pass `prefix_reuse` explicitly for advice that does
    not depend on ambient state.

    Note that `prefix_reuse=0.0` and `prefix_reuse=None` are distinct inputs:
    0.0 vetoes offloading (reuse was measured and absent), None defers to the
    router and then to the heuristic. Never collapse them with `or`.
    """
    notes: list[str] = []

    if prefix_reuse is None:
        prefix_reuse = measured_prefix_reuse()

    if vendor and vendor.lower() not in SUPPORTED_VENDORS:
        return OffloadAdvice(
            recommended=False,
            reason=f"OffloadingConnector supports {'/'.join(SUPPORTED_VENDORS)} "
                   f"accelerators; nothing to offload from on '{vendor}'",
        )

    if host_ram_mb <= 0:
        return OffloadAdvice(
            recommended=False,
            reason="host RAM unknown — refusing to size a pinned-memory buffer blind "
                   "(pinned memory is unswappable; a bad guess destabilizes the host)",
        )

    # Measured reuse, when available, is the whole ballgame: offloading buys
    # prefix hits and nothing else.
    if prefix_reuse is not None and prefix_reuse < 0.1:
        return OffloadAdvice(
            recommended=False,
            reason=f"measured prefix reuse is {prefix_reuse:.0%} — offloading extends "
                   "prefix-cache capacity, which a workload with no shared prefixes "
                   "cannot use",
        )

    cpu_bytes = size_cpu_tier(host_ram_mb, gpu_kv_mb)
    if cpu_bytes == 0:
        free_after = host_ram_mb - KV_OFFLOAD_MIN_FREE_RAM_MB
        if free_after <= 0:
            reason = (f"host has {host_ram_mb}MB RAM; pinning any of it would leave "
                      f"under the {KV_OFFLOAD_MIN_FREE_RAM_MB}MB floor")
        elif gpu_kv_mb > 0 and min(host_ram_mb * KV_OFFLOAD_HOST_RAM_FRACTION,
                                   free_after) < gpu_kv_mb:
            reason = (f"safe host budget is smaller than the {gpu_kv_mb:.0f}MB GPU KV "
                      "cache it would back — the extra tier would not extend it")
        else:
            reason = (f"safe host budget is under the {KV_OFFLOAD_MIN_BYTES // 1024**3}GiB "
                      "floor where the connector's overhead pays for itself")
        return OffloadAdvice(recommended=False, reason=reason)

    if prefix_reuse is not None:
        notes.append(f"measured prefix reuse {prefix_reuse:.0%} (from this process's "
                     "prefix-aware routing) — offloaded blocks should see comparable "
                     "hit rates")
    else:
        notes.append("no measured prefix reuse available; this assumes a prefix-heavy "
                     "workload (multi-turn chat, shared RAG system prompt, agent loops). "
                     "Single-shot prompts with no shared prefix gain nothing")
    notes.append(f"{cpu_bytes / 1024**3:.1f}GiB is PINNED host memory — unswappable and "
                 f"unavailable to the OS while the engine runs (host has {host_ram_mb / 1024:.1f}GiB)")
    notes.append("extends the prefix cache only; it does not make a model that "
                 "exceeds VRAM fit")
    if gpu_kv_mb > 0:
        ratio = (cpu_bytes / 1024**2) / gpu_kv_mb
        notes.append(f"CPU tier is {ratio:.1f}x the {gpu_kv_mb:.0f}MB GPU KV cache")

    return OffloadAdvice(
        recommended=True,
        cpu_bytes=cpu_bytes,
        reason=(f"GPU KV cache is {gpu_kv_mb:.0f}MB; a {cpu_bytes / 1024**3:.1f}GiB host "
                "tier lets evicted prefixes stay cached instead of being recomputed"),
        flag=f"--kv-transfer-config '{build_kv_transfer_config(cpu_bytes)}'",
        notes=notes,
    )

"""MIG partition planner: calculate optimal GPU partitioning.

Given a set of models to serve and available MIG-capable GPUs (A100, H100, H200),
compute the best MIG partition layout that maximizes utilization while meeting
memory requirements.

MIG profiles (A100 80GB example):
  7g.80gb  — full GPU (1 instance)
  4g.40gb  — half GPU (2 instances)
  3g.40gb  — 3/7 GPU (2 instances, different SM count)
  2g.20gb  — 2/7 GPU (3 instances)
  1g.10gb  — 1/7 GPU (7 instances)
  1g.20gb  — 1/7 GPU with more memory (available on some configs)

H100 80GB:
  7g.80gb, 4g.40gb, 3g.40gb, 2g.20gb, 1g.10gb, 1g.20gb
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MIGProfile:
    name: str           # e.g. "1g.10gb"
    gpu_instances: int  # Number of compute slices
    memory_gb: int      # Memory per instance
    max_instances: int  # Max instances of this profile per GPU


@dataclass
class ModelRequirement:
    name: str
    vram_gb: int
    priority: int = 1      # Higher = more important
    min_compute: int = 1   # Minimum GPU instance slices needed


@dataclass
class PartitionPlan:
    gpu_index: int
    gpu_name: str
    total_vram_gb: int
    partitions: list[dict[str, str]] = field(default_factory=list)  # [{profile, model, vram_gb}]
    utilization: float = 0.0
    waste_gb: int = 0


# Known MIG profiles
MIG_PROFILES = {
    "A100-80GB": [
        MIGProfile("7g.80gb", 7, 80, 1),
        MIGProfile("4g.40gb", 4, 40, 1),
        MIGProfile("3g.40gb", 3, 40, 2),
        MIGProfile("2g.20gb", 2, 20, 3),
        MIGProfile("1g.10gb", 1, 10, 7),
        MIGProfile("1g.20gb", 1, 20, 4),
    ],
    "A100-40GB": [
        MIGProfile("7g.40gb", 7, 40, 1),
        MIGProfile("4g.20gb", 4, 20, 1),
        MIGProfile("3g.20gb", 3, 20, 2),
        MIGProfile("2g.10gb", 2, 10, 3),
        MIGProfile("1g.5gb", 1, 5, 7),
    ],
    "H100-80GB": [
        MIGProfile("7g.80gb", 7, 80, 1),
        MIGProfile("4g.40gb", 4, 40, 1),
        MIGProfile("3g.40gb", 3, 40, 2),
        MIGProfile("2g.20gb", 2, 20, 3),
        MIGProfile("1g.10gb", 1, 10, 7),
        MIGProfile("1g.20gb", 1, 20, 4),
    ],
    "H200-141GB": [
        MIGProfile("7g.141gb", 7, 141, 1),
        MIGProfile("4g.71gb", 4, 71, 1),
        MIGProfile("3g.71gb", 3, 71, 2),
        MIGProfile("2g.35gb", 2, 35, 3),
        MIGProfile("1g.18gb", 1, 18, 7),
    ],
}


def get_gpu_type(gpu_name: str) -> str:
    """Map GPU name to MIG profile key."""
    name = gpu_name.upper()
    if "H200" in name:
        return "H200-141GB"
    if "H100" in name:
        return "H100-80GB"
    if "A100" in name:
        if "40" in name:
            return "A100-40GB"
        return "A100-80GB"
    return ""


def plan_partitions(
    gpu_name: str,
    gpu_index: int,
    models: list[ModelRequirement],
) -> PartitionPlan:
    """Compute optimal MIG partition layout for a set of models.

    Uses a greedy bin-packing approach:
    1. Sort models by VRAM requirement (largest first)
    2. For each model, find the smallest MIG profile that fits
    3. Track remaining GPU capacity
    """
    gpu_type = get_gpu_type(gpu_name)
    profiles = MIG_PROFILES.get(gpu_type, [])

    if not profiles:
        return PartitionPlan(
            gpu_index=gpu_index, gpu_name=gpu_name, total_vram_gb=0,
            partitions=[{"error": f"No MIG profiles for {gpu_name}"}],
        )

    total_vram = profiles[0].memory_gb  # Full GPU profile
    plan = PartitionPlan(
        gpu_index=gpu_index, gpu_name=gpu_name, total_vram_gb=total_vram,
    )

    # Sort models by VRAM (largest first), then priority
    sorted_models = sorted(models, key=lambda m: (-m.vram_gb, -m.priority))

    # Track used slices (total = 7 for all current MIG GPUs)
    remaining_slices = 7
    allocated_vram = 0

    for model in sorted_models:
        if remaining_slices <= 0:
            break

        # Find smallest profile that fits this model
        best_profile: MIGProfile | None = None
        for p in sorted(profiles, key=lambda p: p.memory_gb):
            if p.memory_gb >= model.vram_gb and p.gpu_instances <= remaining_slices:
                if model.min_compute <= p.gpu_instances:
                    best_profile = p
                    break

        if best_profile:
            plan.partitions.append({
                "profile": best_profile.name,
                "model": model.name,
                "vram_gb": str(best_profile.memory_gb),
                "slices": str(best_profile.gpu_instances),
            })
            remaining_slices -= best_profile.gpu_instances
            allocated_vram += best_profile.memory_gb
        else:
            plan.partitions.append({
                "profile": "none",
                "model": model.name,
                "vram_gb": str(model.vram_gb),
                "status": "does not fit",
            })

    plan.utilization = allocated_vram / total_vram if total_vram > 0 else 0
    plan.waste_gb = total_vram - allocated_vram

    return plan


def generate_mig_commands(plan: PartitionPlan) -> list[str]:
    """Generate nvidia-smi MIG CLI commands to apply a partition plan."""
    commands: list[str] = []
    gpu = plan.gpu_index

    # Enable MIG mode
    commands.append(f"sudo nvidia-smi -i {gpu} -mig 1")

    # Create GPU instances
    for p in plan.partitions:
        profile = p.get("profile", "")
        if profile and profile != "none":
            # Extract GPU instance profile ID from profile name
            int(p.get("slices", "1"))
            commands.append(
                f"sudo nvidia-smi mig -i {gpu} -cgi {profile} -C"
            )

    return commands

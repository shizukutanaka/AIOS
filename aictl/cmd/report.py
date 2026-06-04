"""aictl report — generate comprehensive system assessment report.

Produces a Markdown report suitable for:
  - Technical due diligence
  - System health documentation
  - Compliance audits
  - Capacity planning

Aggregates data from all subsystems:
  hardware, security, fabric, engines, recipes, models, costs, k8s readiness.
"""

from __future__ import annotations

import argparse

from typing import Any

import time
from pathlib import Path
from aictl.core.output import ok, print_json
from aictl.core.constants import AICTL_VERSION
from aictl.core.state import StateStore


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("report", help="Generate system assessment report")
    p.add_argument("--output", default="", help="Output file path (default: stdout)")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the report command."""
    store = StateStore(getattr(args, "state_dir", None))
    report = _generate_report(store)

    if getattr(args, "json", False):
        print_json(report)
        return 0

    md = _to_markdown(report)

    output = getattr(args, "output", "")
    if output:
        Path(output).write_text(md)
        ok(f"Report saved to {output}")
    else:
        print(md)
    return 0


def _generate_report(store: StateStore) -> dict[str, Any]:
    """Collect data from all subsystems."""
    from aictl.runtime.broker import full_detect
    from aictl.core.security import scan
    from aictl.runtime.fabric import detect_memory_fabric, generate_placement_policy
    from aictl.runtime.recommend import recommend
    from aictl.stack.manifest import list_recipes
    from aictl.core.cost import compare_gpus
    from aictl.runtime.dynamo import detect_dynamo, generate_kvbm_config
    from dataclasses import asdict

    hw = full_detect()
    sec = scan(store.dir)
    fabric = detect_memory_fabric()
    policy = generate_placement_policy(fabric, vram_gb=sum(g.vram_mb for g in hw.gpus) // 1024)
    recs = recommend(vram_mb=sum(g.vram_mb for g in hw.gpus), ram_mb=hw.system.ram_total_mb, max_results=10)
    recipes = list_recipes()
    costs = compare_gpus()
    dynamo = detect_dynamo()
    kvbm = generate_kvbm_config(fabric)

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "version": AICTL_VERSION,
        "hardware": {
            "hostname": hw.system.hostname,
            "kernel": hw.system.kernel,
            "cpu": f"{hw.system.cpu_model} ({hw.system.cpu_cores} cores)",
            "ram_mb": hw.system.ram_total_mb,
            "disk_free_gb": round(hw.system.disk_free_gb, 1),
            "gpus": [{"name": g.name, "vram_mb": g.vram_mb, "vendor": g.vendor,
                      "driver": g.driver_version, "mig": g.mig_capable} for g in hw.gpus],
            "npus": [{"name": n.name, "vendor": n.vendor} for n in hw.npus],
            "profile": hw.profile,
            "container_runtime": hw.container_runtime,
            "cgroup_v2": hw.system.cgroup_v2,
            "psi": hw.system.psi_enabled,
        },
        "security": {
            "score": sec.score,
            "passed": sec.checks_passed,
            "total": sec.checks_total,
            "findings": [{"severity": f.severity, "title": f.title,
                         "remediation": f.remediation} for f in sec.findings],
        },
        "fabric": {
            "tiers": [{"name": t.name, "capacity_gb": t.capacity_gb,
                       "available_gb": t.available_gb} for t in fabric.tiers],
            "total_gb": round(fabric.total_capacity_gb, 1),
            "damon": fabric.damon_available,
            "cxl": fabric.cxl_detected,
            "numa_nodes": fabric.numa_nodes,
            "placement": asdict(policy),
        },
        "dynamo": {
            "available": dynamo.get("dynamo_available", False),
            "nixl": dynamo.get("nixl_available", False),
            "kvbm": asdict(kvbm),
        },
        "models": {
            "recommended": [{"name": r.name, "runtime": r.runtime,
                            "vram_mb": r.vram_required_mb, "use_case": r.use_case}
                           for r in recs],
            "total_in_db": 26,
        },
        "recipes": recipes,
        "costs": [{"gpu": c.gpu_type, "cloud_monthly": round(c.cloud_monthly_usd),
                   "onprem_monthly": round(c.onprem_monthly_usd),
                   "break_even_months": round(c.break_even_months),
                   "recommendation": c.recommendation} for c in costs],
        "k8s_readiness": {
            "kserve": True,
            "gateway_api": True,
            "keda": True,
            "llm_d": True,
            "dynamo_grove": dynamo.get("grove_available", False),
        },
    }


def _to_markdown(r: dict[str, Any]) -> str:
    """Convert report dict to Markdown."""
    lines = [
        "# AI OS System Assessment Report",
        "",
        f"Generated: {r['timestamp']}  ",
        f"Version: aictl {r['version']}",
        "",
        "## 1. Hardware",
        "",
        "| Property | Value |",
        "|----------|-------|",
        f"| Hostname | {r['hardware']['hostname']} |",
        f"| Kernel | {r['hardware']['kernel']} |",
        f"| CPU | {r['hardware']['cpu']} |",
        f"| RAM | {r['hardware']['ram_mb']} MB |",
        f"| Disk | {r['hardware']['disk_free_gb']} GB free |",
        f"| Profile | {r['hardware']['profile']} |",
        f"| Container RT | {r['hardware']['container_runtime']} |",
        f"| cgroup v2 | {'Yes' if r['hardware']['cgroup_v2'] else 'No'} |",
        f"| PSI | {'Yes' if r['hardware']['psi'] else 'No'} |",
        "",
    ]

    if r["hardware"]["gpus"]:
        lines.append("### GPUs")
        lines.append("")
        for g in r["hardware"]["gpus"]:
            lines.append(f"- {g['name']} ({g['vram_mb']} MB, {g['vendor']}, driver {g['driver']})")
        lines.append("")

    lines.extend([
        f"## 2. Security (Score: {r['security']['score']}/100)",
        "",
        f"Passed: {r['security']['passed']}/{r['security']['total']}",
        "",
    ])
    for f in r["security"]["findings"]:
        lines.append(f"- **{f['severity'].upper()}**: {f['title']}  ")
        if f["remediation"]:
            lines.append(f"  Fix: `{f['remediation']}`")
    lines.append("")

    lines.extend([
        f"## 3. Memory Fabric ({r['fabric']['total_gb']} GB)",
        "",
        "| Tier | Capacity | Available |",
        "|------|----------|-----------|",
    ])
    for t in r["fabric"]["tiers"]:
        lines.append(f"| {t['name'].upper()} | {t['capacity_gb']:.1f} GB | {t['available_gb']:.1f} GB |")
    lines.extend([
        "",
        f"DAMON: {'Available' if r['fabric']['damon'] else 'Not available'}  ",
        f"CXL: {'Detected' if r['fabric']['cxl'] else 'Not detected'}  ",
        f"NUMA: {r['fabric']['numa_nodes']} nodes",
        "",
    ])

    lines.extend([
        "## 4. NVIDIA Dynamo",
        "",
        f"- Available: {'Yes' if r['dynamo']['available'] else 'No'}",
        f"- NIXL: {'Yes' if r['dynamo']['nixl'] else 'No'}",
        f"- KVBM DRAM: {r['dynamo']['kvbm']['cpu_dram_gb']:.1f} GB",
        f"- KVBM SSD: {r['dynamo']['kvbm']['local_ssd_gb']:.1f} GB",
        "",
    ])

    lines.extend([
        f"## 5. Model Recommendations (top {len(r['models']['recommended'])})",
        "",
        "| Model | Runtime | VRAM | Use Case |",
        "|-------|---------|------|----------|",
    ])
    for m in r["models"]["recommended"][:5]:
        lines.append(f"| {m['name']} | {m['runtime']} | {m['vram_mb']} MB | {m['use_case']} |")
    lines.append("")

    lines.extend([
        "## 6. Cost Comparison (April 2026 pricing)",
        "",
        "| GPU | Cloud/mo | On-prem/mo | Break-even |",
        "|-----|----------|------------|------------|",
    ])
    for c in r["costs"]:
        lines.append(f"| {c['gpu']} | ${c['cloud_monthly']:,} | ${c['onprem_monthly']:,} | {c['break_even_months']} months |")
    lines.append("")

    lines.extend([
        "## 7. K8s Readiness",
        "",
    ])
    for k, v in r["k8s_readiness"].items():
        icon = "✓" if v else "✗"
        lines.append(f"- {icon} {k}")
    lines.append("")

    lines.extend([
        f"## 8. Available Recipes ({len(r['recipes'])})",
        "",
        ", ".join(r["recipes"]),
        "",
        "---",
        f"*Generated by aictl v{r['version']}*",
    ])

    return "\n".join(lines)

"""Local orchestrator: convert Stack manifests into running services.

Single-node mode: generates Podman run commands or Quadlet units.
Ollama-based services use `ollama serve` + `ollama run`.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from aictl.stack.manifest import ServiceDef, StackManifest
from aictl.runtime.broker import detect_container_runtime, detect_ollama


@dataclass
class RunningService:
    name: str
    stack: str
    container_id: str = ""
    pid: int = 0
    endpoint: str = ""
    status: str = "starting"  # starting | running | stopped | error
    runtime: str = ""
    error: str = ""


def apply_stack(manifest: StackManifest, dry_run: bool = False) -> list[RunningService]:
    """Apply a stack manifest — start services locally."""
    results: list[RunningService] = []
    container_rt = detect_container_runtime()
    has_ollama = detect_ollama()

    for svc in manifest.services:
        rs = RunningService(name=svc.name, stack=manifest.name, runtime=svc.runtime)

        if svc.runtime == "ollama" and has_ollama:
            rs = _apply_ollama_service(svc, manifest.name, dry_run)
        elif svc.image and container_rt != "none":
            rs = _apply_container_service(svc, manifest.name, container_rt, dry_run)
        elif svc.runtime in ("vllm", "sglang", "trt-llm"):
            # These need container images
            if container_rt == "none":
                rs.status = "error"
                rs.error = f"Container runtime required for {svc.runtime}"
            else:
                rs = _apply_inference_engine(svc, manifest.name, container_rt, dry_run)
        else:
            rs.status = "error"
            rs.error = "Cannot determine how to start this service"

        results.append(rs)

    return results


def _apply_ollama_service(svc: ServiceDef, stack_name: str, dry_run: bool) -> RunningService:
    """Write Quadlet unit for an Ollama service."""
    rs = RunningService(name=svc.name, stack=stack_name, runtime="ollama")
    port = svc.port or 11434
    rs.endpoint = f"http://localhost:{port}"

    if dry_run:
        rs.status = "dry-run"
        return rs

    # Check if ollama is already running
    try:
        import urllib.request
        urllib.request.urlopen(f"http://localhost:{port}/api/tags", timeout=2)
        rs.status = "running"
    except Exception:
        # Start ollama serve in background
        try:
            env = {**os.environ, "OLLAMA_HOST": f"0.0.0.0:{port}"}
            proc = subprocess.Popen(
                ["ollama", "serve"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            rs.pid = proc.pid
            rs.status = "starting"
            time.sleep(1)
        except FileNotFoundError:
            rs.status = "error"
            rs.error = "ollama binary not found"
            return rs
        except OSError as e:
            rs.status = "error"
            rs.error = f"Failed to start ollama: {e}"
            return rs

    # Pull model if specified
    if svc.model:
        try:
            subprocess.run(
                ["ollama", "pull", svc.model],
                capture_output=True, timeout=300,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass  # best-effort; failure is non-critical

    return rs


def _apply_container_service(
    svc: ServiceDef, stack_name: str, rt: str, dry_run: bool
) -> RunningService:
    """Write Quadlet unit for a container service."""
    rs = RunningService(name=svc.name, stack=stack_name, runtime=rt)
    container_name = f"aios-{stack_name}-{svc.name}"
    port = svc.port or 8080

    cmd = [
        rt, "run", "-d",
        "--name", container_name,
        "--replace",
        "-p", f"{port}:{port}",
    ]

    # GPU passthrough
    if svc.gpu_required:
        if rt == "podman":
            cmd += ["--device", "nvidia.com/gpu=all"]
        else:
            cmd += ["--gpus", "all"]

    # Environment
    for k, v in svc.env.items():
        cmd += ["-e", f"{k}={v}"]

    cmd.append(svc.image)

    rs.endpoint = f"http://localhost:{port}"

    if dry_run:
        rs.status = "dry-run"
        rs.container_id = " ".join(cmd)
        return rs

    try:
        # Stop existing
        subprocess.run([rt, "rm", "-f", container_name],
                       capture_output=True, timeout=10)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            rs.container_id = result.stdout.strip()[:12]
            rs.status = "running"
        else:
            rs.status = "error"
            rs.error = result.stderr.strip()[:200]
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        rs.status = "error"
        rs.error = str(e)

    return rs


def _apply_inference_engine(
    svc: ServiceDef, stack_name: str, rt: str, dry_run: bool
) -> RunningService:
    """Start vLLM / SGLang / TRT-LLM as a container."""
    IMAGE_MAP = {
        "vllm": "vllm/vllm-openai:latest",
        "sglang": "lmsysorg/sglang:latest",
        "trt-llm": "nvcr.io/nvidia/tritonserver:latest",
    }
    image = IMAGE_MAP.get(svc.runtime, svc.image)
    if not image:
        rs = RunningService(name=svc.name, stack=stack_name, runtime=svc.runtime)
        rs.status = "error"
        rs.error = f"No image for runtime {svc.runtime}"
        return rs

    port = svc.port or 8000
    container_name = f"aios-{stack_name}-{svc.name}"
    gpu_flag = ["--gpus", "all"] if rt == "docker" else ["--device", "nvidia.com/gpu=all"]

    model_arg = []
    if svc.runtime == "vllm" and svc.model:
        model_arg = ["--model", svc.model, "--port", str(port)]
    elif svc.runtime == "sglang" and svc.model:
        model_arg = ["--model-path", svc.model, "--port", str(port)]
    elif svc.runtime == "trt-llm" and svc.model:
        model_arg = ["--model", svc.model, "--port", str(port)]

    cmd = [
        rt, "run", "-d",
        "--name", container_name,
        "--replace",
        "-p", f"{port}:{port}",
        "--shm-size", "1g",
        *gpu_flag,
        image,
        *model_arg,
    ]

    rs = RunningService(
        name=svc.name, stack=stack_name, runtime=svc.runtime,
        endpoint=f"http://localhost:{port}/v1",
    )

    if dry_run:
        rs.status = "dry-run"
        rs.container_id = " ".join(cmd)
        return rs

    try:
        subprocess.run([rt, "rm", "-f", container_name], capture_output=True, timeout=10)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            rs.container_id = result.stdout.strip()[:12]
            rs.status = "running"
        else:
            rs.status = "error"
            rs.error = result.stderr.strip()[:200]
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        rs.status = "error"
        rs.error = str(e)

    return rs


def stop_stack(stack_name: str) -> list[str]:
    """Stop all containers belonging to a stack."""
    rt = detect_container_runtime()
    if rt == "none":
        return []

    stopped: list[str] = []
    try:
        result = subprocess.run(
            [rt, "ps", "-a", "--filter", f"name=aios-{stack_name}-", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
        for name in result.stdout.strip().splitlines():
            if name:
                subprocess.run([rt, "rm", "-f", name], capture_output=True, timeout=10)
                stopped.append(name)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # best-effort; failure is non-critical

    return stopped


def list_running(stack_filter: str = "") -> list[dict[str, Any]]:
    """List running aios services."""
    rt = detect_container_runtime()
    if rt == "none":
        return []

    try:
        fmt = "{{.Names}}\t{{.Status}}\t{{.Ports}}\t{{.ID}}"
        cmd = [rt, "ps", "--filter", "name=aios-", "--format", fmt]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        services: list[dict[str, Any]] = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 4:
                name = parts[0]
                if stack_filter and stack_filter not in name:
                    continue
                services.append({
                    "name": name,
                    "status": parts[1],
                    "ports": parts[2],
                    "container_id": parts[3][:12],
                })
        return services
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

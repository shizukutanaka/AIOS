"""Stack manifest: parse, validate, and resolve AI service declarations.

Supports JSON and TOML. YAML optional (if PyYAML installed).
A Stack defines inference services, models, GPU slices, and policies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ServiceDef:
    name: str
    image: str = ""
    runtime: str = "auto"     # vllm | ollama | sglang | trt-llm | auto
    model: str = ""
    port: int = 0
    gpu_required: bool = False
    gpu_memory_mb: int = 0
    env: dict[str, str] = field(default_factory=dict)
    replicas: int = 1
    health_path: str = "/health"


@dataclass
class ModelRef:
    name: str
    source: str = ""          # registry URL or local path
    digest: str = ""
    format: str = "gguf"      # gguf | safetensors | onnx
    signed: bool = False


@dataclass
class StackManifest:
    name: str
    version: str = "1"
    services: list[ServiceDef] = field(default_factory=list)
    models: list[ModelRef] = field(default_factory=list)
    gpu_slice_policy: str = "auto"  # auto | dedicated | shared
    trust_policy: str = "warn"      # enforce | warn | disabled
    source_file: str = ""


class StackParseError(Exception):
    pass


def parse_file(path: str | Path) -> StackManifest:
    """Parse a stack manifest from file (JSON, TOML, or YAML)."""
    p = Path(path)
    if not p.exists():
        raise StackParseError(f"File not found: {path}")

    text = p.read_text()
    ext = p.suffix.lower()

    if ext == ".json":
        data = json.loads(text)
    elif ext == ".toml":
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef,import-not-found]
        data = tomllib.loads(text)
    elif ext in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped]
            data = yaml.safe_load(text)
        except ImportError:
            raise StackParseError("PyYAML not installed — use JSON or TOML")
    else:
        # Try JSON first, then TOML
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise StackParseError(f"Unsupported format: {ext}")

    return _build_manifest(data, str(p))


def _build_manifest(data: dict[str, Any], source: str) -> StackManifest:
    """Construct and return the requested object."""
    if not isinstance(data, dict):
        raise StackParseError("Stack manifest must be a JSON/TOML/YAML object")

    name = data.get("name", "")
    if not name:
        raise StackParseError("Stack manifest requires 'name' field")

    services: list[ServiceDef] = []
    for sd in data.get("services", []):
        svc_name = sd.get("name", "")
        if not svc_name:
            raise StackParseError("Each service entry must have a non-empty 'name' field")
        services.append(ServiceDef(
            name=svc_name,
            image=sd.get("image", ""),
            runtime=sd.get("runtime", "auto"),
            model=sd.get("model", ""),
            port=sd.get("port", 0),
            gpu_required=sd.get("gpu_required", False),
            gpu_memory_mb=sd.get("gpu_memory_mb", 0),
            env=sd.get("env", {}),
            replicas=sd.get("replicas", 1),
            health_path=sd.get("health_path", "/health"),
        ))

    models: list[ModelRef] = []
    for md in data.get("models", []):
        model_name = md.get("name", "")
        if not model_name:
            raise StackParseError("Each model entry must have a non-empty 'name' field")
        models.append(ModelRef(
            name=model_name,
            source=md.get("source", ""),
            digest=md.get("digest", ""),
            format=md.get("format", "gguf"),
            signed=md.get("signed", False),
        ))

    return StackManifest(
        name=name,
        version=data.get("version", "1"),
        services=services,
        models=models,
        gpu_slice_policy=data.get("gpu_slice_policy", "auto"),
        trust_policy=data.get("trust_policy", "warn"),
        source_file=source,
    )


# ── Built-in recipes ───────────────────────────────────

RECIPES: dict[str, dict[str, Any]] = {
    "local-chat": {
        "name": "local-chat",
        "version": "1",
        "services": [
            {
                "name": "llm",
                "runtime": "ollama",
                "model": "llama3.2:3b",
                "port": 11434,
                "gpu_required": False,
                "health_path": "/api/tags",
            },
            {
                "name": "webui",
                "image": "ghcr.io/open-webui/open-webui:main",
                "port": 3000,
                "env": {"OLLAMA_API_BASE_URL": "http://localhost:11434"},
            },
        ],
        "models": [
            {"name": "llama3.2:3b", "format": "gguf"},
        ],
    },
    "team-rag": {
        "name": "team-rag",
        "version": "1",
        "services": [
            {
                "name": "llm",
                "runtime": "vllm",
                "model": "meta-llama/Llama-3.2-8B-Instruct",
                "port": 8000,
                "gpu_required": True,
                "gpu_memory_mb": 16384,
            },
            {
                "name": "embedding",
                "runtime": "ollama",
                "model": "nomic-embed-text",
                "port": 11435,
            },
            {
                "name": "webui",
                "image": "ghcr.io/open-webui/open-webui:main",
                "port": 3000,
                "env": {"OPENAI_API_BASE_URL": "http://localhost:8000/v1"},
            },
        ],
        "trust_policy": "warn",
    },
    "image-gen": {
        "name": "image-gen",
        "version": "1",
        "services": [
            {
                "name": "diffusion",
                "image": "ghcr.io/comfyanonymous/comfyui:latest",
                "port": 8188,
                "gpu_required": True,
                "gpu_memory_mb": 8192,
            },
        ],
    },
    "code-assist": {
        "name": "code-assist",
        "version": "1",
        "services": [
            {
                "name": "llm",
                "runtime": "vllm",
                "model": "Qwen/Qwen2.5-Coder-7B-Instruct",
                "port": 8000,
                "gpu_required": True,
                "gpu_memory_mb": 16384,
                "health_path": "/health",
            },
            {
                "name": "tabby",
                "image": "tabbyml/tabby:latest",
                "port": 8080,
                "gpu_required": True,
                "env": {"TABBY_MODEL": "Qwen/Qwen2.5-Coder-7B"},
            },
        ],
        "trust_policy": "warn",
    },
    "whisper-stt": {
        "name": "whisper-stt",
        "version": "1",
        "services": [
            {
                "name": "whisper",
                "image": "fedirz/faster-whisper-server:latest-cuda",
                "port": 8000,
                "gpu_required": True,
                "gpu_memory_mb": 4096,
                "env": {"WHISPER__MODEL": "large-v3"},
                "health_path": "/health",
            },
        ],
    },
    "embedding-only": {
        "name": "embedding-only",
        "version": "1",
        "services": [
            {
                "name": "embedding",
                "runtime": "ollama",
                "model": "nomic-embed-text",
                "port": 11434,
                "health_path": "/api/tags",
            },
        ],
    },
    "local-gpu-chat": {
        "name": "local-gpu-chat",
        "version": "1",
        "services": [
            {
                "name": "llm",
                "runtime": "vllm",
                "model": "meta-llama/Llama-3.2-8B-Instruct",
                "port": 8000,
                "gpu_required": True,
                "gpu_memory_mb": 16384,
            },
            {
                "name": "webui",
                "image": "ghcr.io/open-webui/open-webui:main",
                "port": 3000,
                "env": {"OPENAI_API_BASE_URL": "http://localhost:8000/v1"},
            },
        ],
    },
}


def get_recipe(name: str) -> StackManifest | None:
    """Get recipe."""
    data = RECIPES.get(name)
    if data is None:
        return None
    return _build_manifest(data, f"<built-in:{name}>")


def list_recipes() -> list[str]:
    """List recipes."""
    return list(RECIPES.keys())


# bank_to_bugyo integration recipe (Mizuho → Kanjyo Bugyo conversion)
RECIPES["bank-convert"] = {
    "name": "bank-convert",
    "version": "1",
    "services": [
        {
            "name": "llm",
            "runtime": "ollama",
            "model": "qwen2.5:7b",
            "port": 11434,
            "health_path": "/api/tags",
        },
    ],
    "models": [
        {"name": "qwen2.5:7b", "format": "gguf"},
    ],
    "trust_policy": "warn",
}


# Multi-model serving (chat + code + embedding on one GPU)
RECIPES["multi-model"] = {
    "name": "multi-model",
    "version": "1",
    "services": [
        {
            "name": "llm-chat",
            "runtime": "ollama",
            "model": "llama3.1:8b",
            "port": 11434,
            "health_path": "/api/tags",
        },
    ],
    "models": [
        {"name": "llama3.1:8b", "format": "gguf"},
        {"name": "qwen2.5-coder:7b", "format": "gguf"},
        {"name": "nomic-embed-text", "format": "gguf"},
    ],
    "trust_policy": "warn",
}

# Translation service (multilingual model)
RECIPES["translate"] = {
    "name": "translate",
    "version": "1",
    "services": [
        {
            "name": "llm",
            "runtime": "ollama",
            "model": "qwen2.5:7b",
            "port": 11434,
            "health_path": "/api/tags",
        },
    ],
    "models": [
        {"name": "qwen2.5:7b", "format": "gguf"},
    ],
    "trust_policy": "warn",
}

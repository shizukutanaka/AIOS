"""Model format detection: identify model file types and compatibility.

Detects:
  - GGUF (llama.cpp / Ollama)
  - SafeTensors (HuggingFace / vLLM / SGLang)
  - ONNX (ONNX Runtime)
  - PyTorch .bin/.pt (legacy)
  - TensorRT engine files

Provides runtime compatibility mapping:
  GGUF → Ollama (native), llama.cpp
  SafeTensors → vLLM, SGLang, HuggingFace TGI
  ONNX → ONNX Runtime, TensorRT
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ModelFormat:
    format: str          # gguf | safetensors | onnx | pytorch | tensorrt | unknown
    version: str = ""    # Format version if detectable
    size_bytes: int = 0
    compatible_runtimes: list[str] | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        """Set defaults for format detection result."""
        if self.compatible_runtimes is None:
            self.compatible_runtimes = RUNTIME_COMPAT.get(self.format, [])
        if self.metadata is None:
            self.metadata = {}


RUNTIME_COMPAT: dict[str, list[str]] = {
    "gguf": ["ollama", "llama.cpp", "llamafile"],
    "safetensors": ["vllm", "sglang", "tgi", "transformers"],
    "onnx": ["onnxruntime", "tensorrt"],
    "pytorch": ["vllm", "sglang", "transformers"],
    "tensorrt": ["tensorrt-llm"],
}

# Magic bytes for format detection
GGUF_MAGIC = b"GGUF"               # First 4 bytes
SAFETENSORS_MIN_HDR = 8             # header is uint64-le length + JSON
ONNX_MAGIC = b"\x08"               # Protobuf field 1
PYTORCH_MAGIC = b"PK"              # ZIP archive (PyTorch saves as ZIP)


def detect_format(path: str | Path) -> ModelFormat:
    """Detect model file format from file header."""
    path = Path(path)

    if not path.exists():
        return ModelFormat(format="unknown", metadata={"error": "File not found"})

    size = path.stat().st_size

    # Read first 16 bytes for magic detection
    try:
        with open(path, "rb") as f:
            header = f.read(16)
    except OSError as e:
        return ModelFormat(format="unknown", size_bytes=size,
                          metadata={"error": str(e)})

    # GGUF detection
    if header[:4] == GGUF_MAGIC:
        version = ""
        if len(header) >= 8:
            ver = struct.unpack("<I", header[4:8])[0]
            version = f"v{ver}"
        return ModelFormat(format="gguf", version=version, size_bytes=size)

    # Extension-based detection (reliable for most formats)
    ext = path.suffix.lower()

    if ext == ".safetensors":
        return ModelFormat(format="safetensors", size_bytes=size)

    if ext == ".gguf":
        return ModelFormat(format="gguf", size_bytes=size)

    if ext in (".onnx",):
        return ModelFormat(format="onnx", size_bytes=size)

    if ext in (".pt", ".pth", ".bin"):
        # Check if it's a ZIP (PyTorch format)
        if header[:2] == PYTORCH_MAGIC:
            return ModelFormat(format="pytorch", size_bytes=size)
        return ModelFormat(format="pytorch", size_bytes=size)

    if ext == ".engine":
        return ModelFormat(format="tensorrt", size_bytes=size)

    # Try content-based detection for unknown extensions
    if header[:4] == GGUF_MAGIC:
        return ModelFormat(format="gguf", size_bytes=size)
    if header[:2] == PYTORCH_MAGIC:
        return ModelFormat(format="pytorch", size_bytes=size)
    # SafeTensors: first 8 bytes are a little-endian uint64 JSON-header length.
    # The file never starts with b"{"; checking that byte is always wrong.
    if len(header) >= SAFETENSORS_MIN_HDR:
        hdr_len = struct.unpack("<Q", header[:8])[0]
        if 0 < hdr_len < 10_000_000:  # plausible JSON header (< 10 MB)
            return ModelFormat(format="safetensors", size_bytes=size)

    return ModelFormat(format="unknown", size_bytes=size)


def detect_model_dir(directory: str | Path) -> list[ModelFormat]:
    """Scan a directory for model files and detect their formats."""
    directory = Path(directory)
    if not directory.is_dir():
        return []

    results: list[ModelFormat] = []
    model_extensions = {".gguf", ".safetensors", ".onnx", ".pt", ".pth",
                        ".bin", ".engine", ".model"}

    for f in directory.rglob("*"):
        if f.is_file() and f.suffix.lower() in model_extensions:
            result = detect_format(f)
            result.metadata["path"] = str(f)
            results.append(result)

    return results


def recommend_runtime(fmt: ModelFormat) -> str:
    """Recommend the best runtime for a given model format."""
    if fmt.format == "gguf":
        return "ollama"
    elif fmt.format == "safetensors":
        return "vllm"
    elif fmt.format == "onnx":
        return "onnxruntime"
    elif fmt.format == "pytorch":
        return "vllm"
    elif fmt.format == "tensorrt":
        return "tensorrt-llm"
    return "unknown"


def format_size(size_bytes: int) -> str:
    """Format file size for display."""
    if size_bytes >= 1024**3:
        return f"{size_bytes / 1024**3:.1f} GB"
    elif size_bytes >= 1024**2:
        return f"{size_bytes / 1024**2:.1f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"

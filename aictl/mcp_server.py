"""aictl MCP Server — Model Context Protocol interface.

Exposes aictl's infrastructure management tools to MCP clients
(Claude Desktop, Cursor, VS Code, custom agents).

MCP (Anthropic, donated to Linux Foundation AAIF Dec 2025) is the
de facto standard for LLM ↔ tool integration. This server lets
any MCP-compatible AI assistant manage local inference infrastructure.

Transport: JSON-RPC 2.0 over stdio (stdin/stdout)
Spec: https://modelcontextprotocol.io/specification/2025-11-25

Tools exposed:
  aictl_health       — System health check
  aictl_recommend    — Hardware-aware model recommendations
  aictl_models       — List available/running models
  aictl_cost         — GPU cost comparison
  aictl_deploy       — Deploy a recipe
  aictl_optimize     — Generate optimal vLLM flags
  aictl_status       — Node status and profile
  aictl_security     — Security scan

Usage:
  # In Claude Desktop config (~/.config/claude/mcp_servers.json):
  {
    "aictl": {
      "command": "python3",
      "args": ["-m", "aictl.mcp_server"]
    }
  }
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import deque
from typing import Any


# ── MCP Protocol Constants ──
JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "aictl"
SERVER_VERSION = "1.6.0"


# ── Tool Definitions ──
TOOLS = [
    {
        "name": "aictl_health",
        "description": "Check AI OS system health: daemon status, GPU detection, container runtime, security score",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "aictl_recommend",
        "description": "Recommend AI models that fit current hardware (VRAM, RAM). Returns top models with runtime and VRAM requirements",
        "inputSchema": {
            "type": "object",
            "properties": {
                "use_case": {
                    "type": "string",
                    "description": "Filter: chat, code, embedding, vision, stt",
                    "enum": ["", "chat", "code", "embedding", "vision", "stt"],
                },
                "max_results": {"type": "integer", "default": 5},
            },
        },
    },
    {
        "name": "aictl_cost",
        "description": "Compare GPU cloud vs on-prem costs (RTX 4090, A100, H100, H200, B200, GB200). Returns monthly cost, break-even, cost per million tokens",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "aictl_optimize",
        "description": "Generate optimal vLLM serve flags for a model and GPU. Handles KV cache quantization, tensor parallelism, chunked prefill",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model name (e.g. meta-llama/Llama-3.1-8B-Instruct)"},
                "model_size_b": {"type": "number", "description": "Model size in billions of parameters"},
                "gpu": {"type": "string", "description": "GPU type (H100, B200, RTX 4090, etc.)"},
                "gpu_count": {"type": "integer", "default": 1},
                "objective": {"type": "string", "enum": ["balanced", "throughput", "interactivity"]},
            },
            "required": ["model", "model_size_b"],
        },
    },
    {
        "name": "aictl_security",
        "description": "Run security scan on the AI OS installation. Returns score (0-100) and findings with remediation steps",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "aictl_status",
        "description": "Get node status: hostname, GPU profile, container runtime, OS info",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "aictl_recipes",
        "description": "List available deployment recipes (local-chat, team-rag, code-assist, etc.)",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "aictl_meter",
        "description": "Get token usage and cost per API key/tenant. Shows prompt/completion tokens, daily usage, and estimated cost",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "aictl_lora",
        "description": "List registered LoRA adapters and VRAM budget for base models",
        "inputSchema": {
            "type": "object",
            "properties": {
                "base_model": {"type": "string", "default": "", "description": "Filter by base model"},
            },
        },
    },
    {
        "name": "aictl_fabric",
        "description": "Detect memory fabric tiers (DRAM/CXL/NVMe) and placement policy for AI workloads",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    # ── v1.7.0 tools ──────────────────────────────────
    {
        "name": "aictl_eval",
        "description": "Run LLM regression tests from an eval suite. Catches prompt quality regressions before deployment. Pass assertions as a list.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cases": {
                    "type": "array",
                    "description": "List of test cases, each with id, prompt, and assertions",
                    "items": {"type": "object"}
                },
                "model": {"type": "string", "default": "auto"},
            },
            "required": ["cases"],
        },
    },
    {
        "name": "aictl_spec_recommend",
        "description": "Recommend a speculative decoding draft model for 2-3x faster inference. Production standard in vLLM v0.20 + SGLang v0.5.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_model": {"type": "string", "description": "Target (large, slow) model name"},
            },
            "required": ["target_model"],
        },
    },
        # ── v1.6.0 tools ──────────────────────────────────
    {
        "name": "aictl_fit",
        "description": "Check if a specific AI model will fit on a GPU. Shows VRAM at each quantization (FP16/FP8/Q8/AWQ/Q4/Q3) and suggests alternatives.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model name (e.g. llama3:70b, qwen3:7b)"},
                "gpu": {"type": "string", "description": "GPU type (auto to detect)", "default": "auto"},
            },
            "required": ["model"],
        },
    },
    {
        "name": "aictl_quant",
        "description": "Compare quantization formats for a model. Shows quality retention, size, speed for FP16/FP8/AWQ/Q4/GPTQ/Q3.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model name"},
                "use_case": {"type": "string", "enum": ["chat", "code", "reasoning"], "default": "chat"},
            },
            "required": ["model"],
        },
    },
    {
        "name": "aictl_guard_scan",
        "description": "Scan text for PII (email, phone, credit card, SSN, API keys) and content policy violations (prompt injection, jailbreak). Runs locally.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to scan"},
                "redact": {"type": "boolean", "description": "Replace PII with [REDACTED]", "default": False},
            },
            "required": ["text"],
        },
    },
    {
        "name": "aictl_rag_ask",
        "description": "Answer a question using indexed documents as context. Index first with: aictl rag index <path>",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Question to answer"},
                "k": {"type": "integer", "description": "Context chunks", "default": 5},
            },
            "required": ["question"],
        },
    },
    {
        "name": "aictl_troubleshoot",
        "description": "Diagnose AI inference problems (OOM, slow, wrong output, can't start, high cost). Suggests one fix.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symptom": {"type": "string", "enum": ["oom", "slow", "wrong-output", "cant-start", "high-cost", "auto"], "default": "auto"},
            },
        },
    },
    {
        "name": "aictl_tco",
        "description": "True Cost of Ownership: GPU depreciation + electricity + CO₂e emissions vs cloud API pricing. Includes GPU power-cap advisory from FREESH (arXiv:2511.00807).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "period_days": {"type": "integer", "default": 30},
                "carbon_intensity": {"type": "number", "description": "Grid carbon intensity in gCO₂e/kWh (default: 500 world avg)"},
                "region": {"type": "string", "description": "Grid region shortcode for automatic carbon intensity (e.g. jp, us, eu, fr, cn)", "default": "global"},
            },
        },
    },
    {
        "name": "aictl_guided",
        "description": "Recommend a structured/guided-decoding backend (XGrammar/Outlines/llguidance) and ready-to-paste serve flags so a model emits valid JSON. Optionally validate a JSON instance against a JSON Schema locally.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "engine": {"type": "string", "enum": ["vllm", "sglang", "tensorrt-llm", "ollama"], "default": "vllm"},
                "instance": {"type": "object", "description": "Optional JSON instance to validate against `schema`"},
                "schema": {"type": "object", "description": "Optional JSON Schema; if given with `instance`, returns validation errors"},
            },
        },
    },
]


# ── Tool Span Ring Buffer ──
# Bounded ring of the 200 most-recent MCP tool spans; never grows unboundedly.
_TOOL_SPANS: deque = deque(maxlen=200)


def get_tool_spans() -> list:
    """Return a snapshot of the recent MCP tool-call spans (for testing/inspection)."""
    return list(_TOOL_SPANS)


def _first_text(result: dict) -> str:
    """Extract the first text content fragment from an MCP result dict."""
    for item in result.get("content", []):
        if item.get("type") == "text":
            return item.get("text", "")[:200]
    return ""


def handle_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool, record an OTel ToolSpan, and return the MCP result.

    The span is always appended to the in-process ring buffer.  If the env var
    AIOS_OTEL_ENDPOINT is set the span is also fire-and-forget exported via
    OTLP/HTTP.  Observability failures never propagate to the caller.
    """
    start_ns = time.monotonic_ns()
    result = _dispatch_tool(name, arguments)
    end_ns = time.monotonic_ns()

    try:
        from aictl.metrics.genai_spans import ToolSpan, export_tool_spans
        span = ToolSpan(
            tool_name=name,
            success=not result.get("isError", False),
            start_time_ns=start_ns,
            end_time_ns=end_ns,
            error="" if not result.get("isError") else _first_text(result),
        )
        _TOOL_SPANS.append(span)
        endpoint = os.environ.get("AIOS_OTEL_ENDPOINT", "")
        if endpoint:
            export_tool_spans([span], endpoint=endpoint)
    except Exception:
        pass  # observability must never break the serving path

    return result


# ── Tool Handlers ──
def _dispatch_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch to a specific tool implementation and return MCP result."""
    try:
        if name == "aictl_health":
            return _tool_health()
        elif name == "aictl_recommend":
            return _tool_recommend(arguments)
        elif name == "aictl_cost":
            return _tool_cost()
        elif name == "aictl_optimize":
            return _tool_optimize(arguments)
        elif name == "aictl_security":
            return _tool_security()
        elif name == "aictl_status":
            return _tool_status()
        elif name == "aictl_recipes":
            return _tool_recipes()
        elif name == "aictl_meter":
            return _tool_meter()
        elif name == "aictl_lora":
            return _tool_lora(arguments)
        elif name == "aictl_fabric":
            return _tool_fabric()
        elif name == "aictl_fit":
            return _tool_fit(arguments)
        elif name == "aictl_eval":
            return _tool_eval(arguments)
        elif name == "aictl_spec_recommend":
            return _tool_spec_recommend(arguments)
        elif name == "aictl_quant":
            return _tool_quant(arguments)
        elif name == "aictl_guard_scan":
            return _tool_guard_scan(arguments)
        elif name == "aictl_rag_ask":
            return _tool_rag_ask(arguments)
        elif name == "aictl_troubleshoot":
            return _tool_troubleshoot(arguments)
        elif name == "aictl_tco":
            return _tool_tco(arguments)
        elif name == "aictl_guided":
            return _tool_guided(arguments)
        else:
            return {"content": [{"type": "text", "text": f"Unknown tool: {name}"}], "isError": True}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}


def _tool_health() -> dict[str, Any]:
    """Execute the aictl_health MCP tool and return result dict."""
    from aictl.runtime.broker import full_detect
    from aictl.core.security import scan
    hw = full_detect()
    sec = scan()
    return {"content": [{"type": "text", "text": json.dumps({
        "profile": hw.profile,
        "gpus": len(hw.gpus),
        "npus": len(hw.npus),
        "container_runtime": hw.container_runtime,
        "ram_mb": hw.system.ram_total_mb,
        "security_score": sec.score,
        "security_passed": f"{sec.checks_passed}/{sec.checks_total}",
    }, indent=2)}]}


def _tool_recommend(args: dict[str, Any]) -> dict[str, Any]:
    """Execute aictl_recommend MCP tool and return result dict."""
    from aictl.runtime.recommend import recommend
    from aictl.runtime.broker import full_detect
    hw = full_detect()
    vram = sum(g.vram_mb for g in hw.gpus)
    recs = recommend(
        vram_mb=vram,
        ram_mb=hw.system.ram_total_mb,
        use_case=args.get("use_case", ""),
        max_results=args.get("max_results", 5),
    )
    return {"content": [{"type": "text", "text": json.dumps([
        {"name": r.name, "runtime": r.runtime, "vram_mb": r.vram_required_mb,
         "use_case": r.use_case, "context_length": r.context_length, "notes": r.notes}
        for r in recs
    ], indent=2)}]}


def _tool_cost() -> dict[str, Any]:
    """Execute aictl_cost MCP tool and return result dict."""
    from aictl.core.cost import compare_gpus
    results = compare_gpus()
    return {"content": [{"type": "text", "text": json.dumps([
        {"gpu": r.gpu_type, "cloud_monthly": round(r.cloud_monthly_usd),
         "onprem_monthly": round(r.onprem_monthly_usd),
         "break_even_months": round(r.break_even_months),
         "cost_per_million_tokens": round(r.cost_per_million_tokens, 4),
         "recommendation": r.recommendation}
        for r in results
    ], indent=2)}]}


def _tool_optimize(args: dict[str, Any]) -> dict[str, Any]:
    """Execute aictl_optimize MCP tool and return result dict."""
    from aictl.runtime.optimize import optimize_vllm_flags, flags_to_command, HardwareProfile, GPU_CC
    gpu = args.get("gpu", "H100")
    profile = HardwareProfile(
        gpu_name=gpu,
        gpu_count=args.get("gpu_count", 1),
        vram_per_gpu_mb={"H100": 81920, "B200": 196608, "A100": 81920,
                         "RTX 4090": 24576}.get(gpu, 24576),
        compute_capability=GPU_CC.get(gpu, 90),
    )
    model = args.get("model")
    model_size_b = args.get("model_size_b")
    if not model or model_size_b is None:
        return {"isError": True, "content": [{"type": "text",
                "text": "model and model_size_b are required"}]}
    result = optimize_vllm_flags(
        model=model,
        model_size_b=model_size_b,
        hardware=profile,
        objective=args.get("objective", "balanced"),
    )
    return {"content": [{"type": "text", "text": json.dumps({
        "command": flags_to_command(result),
        "flags": result.flags,
        "estimated_throughput_tps": result.estimated_throughput_tps,
        "kv_cache_dtype": result.kv_cache_dtype,
        "tensor_parallel": result.tensor_parallel,
        "notes": result.notes,
    }, indent=2)}]}


def _tool_security() -> dict[str, Any]:
    """Execute aictl_security MCP tool and return result dict."""
    from aictl.core.security import scan
    report = scan()
    return {"content": [{"type": "text", "text": json.dumps({
        "score": report.score,
        "passed": report.checks_passed,
        "total": report.checks_total,
        "findings": [{"severity": f.severity, "title": f.title,
                      "remediation": f.remediation} for f in report.findings],
    }, indent=2)}]}


def _tool_status() -> dict[str, Any]:
    """Execute aictl_status MCP tool and return result dict."""
    from aictl.runtime.broker import full_detect
    hw = full_detect()
    return {"content": [{"type": "text", "text": json.dumps({
        "hostname": hw.system.hostname,
        "kernel": hw.system.kernel,
        "cpu": f"{hw.system.cpu_model} ({hw.system.cpu_cores} cores)",
        "ram_mb": hw.system.ram_total_mb,
        "profile": hw.profile,
        "container_runtime": hw.container_runtime,
        "gpus": [{"name": g.name, "vram_mb": g.vram_mb} for g in hw.gpus],
        "npus": [{"name": n.name, "vendor": n.vendor} for n in hw.npus],
    }, indent=2)}]}


def _tool_recipes() -> dict[str, Any]:
    """Execute aictl_recipes MCP tool and return result dict."""
    from aictl.stack.manifest import list_recipes, get_recipe
    recipes = []
    for name in list_recipes():
        r = get_recipe(name)
        if r:
            recipes.append({
                "name": name,
                "services": len(r.services),
                "gpu_required": any(s.gpu_required for s in r.services),
            })
    return {"content": [{"type": "text", "text": json.dumps(recipes, indent=2)}]}


def _tool_meter() -> dict[str, Any]:
    """Execute aictl_meter MCP tool and return result dict."""
    from aictl.core.metering import TokenMeter
    from dataclasses import asdict
    meter = TokenMeter()
    buckets = meter.list_usage()
    data = {
        "entities": [asdict(b) for b in buckets],
        "total_tokens": sum(b.total_tokens for b in buckets),
        "total_entities": len(buckets),
    }
    return {"content": [{"type": "text", "text": json.dumps(data, indent=2)}]}


def _tool_lora(args: dict[str, Any]) -> dict[str, Any]:
    """Execute aictl_lora MCP tool and return result dict."""
    from aictl.runtime.lora import LoRAManager
    from dataclasses import asdict
    mgr = LoRAManager()
    adapters = mgr.list_adapters(base_model=args.get("base_model", ""))
    return {"content": [{"type": "text", "text": json.dumps(
        [asdict(a) for a in adapters], indent=2
    )}]}


def _tool_fabric() -> dict[str, Any]:
    """Execute aictl_fabric MCP tool and return result dict."""
    from aictl.runtime.fabric import detect_memory_fabric, generate_placement_policy
    from dataclasses import asdict
    fabric = detect_memory_fabric()
    policy = generate_placement_policy(fabric)
    return {"content": [{"type": "text", "text": json.dumps({
        "tiers": [{"name": t.name, "capacity_gb": t.capacity_gb,
                   "available_gb": t.available_gb, "bandwidth_gbps": t.bandwidth_gbps}
                  for t in fabric.tiers],
        "total_gb": round(fabric.total_capacity_gb, 1),
        "damon": fabric.damon_available,
        "cxl": fabric.cxl_detected,
        "placement": asdict(policy),
    }, indent=2)}]}


# ── v1.6.0 Tool Handlers ──


def _tool_eval(args: dict[str, Any]) -> dict[str, Any]:
    """Run eval cases inline."""
    cases = args.get("cases", [])
    model = args.get("model", "auto")
    if not cases:
        return {"content": [{"type": "text", "text": "No cases provided."}], "isError": True}
    from aictl.sdk import _AmbientContext
    _AmbientContext.reset_for_testing()
    from aictl.cmd.eval import _run_case
    results = [_run_case(c, model) for c in cases]
    passed = sum(1 for r in results if r["passed"])
    lines = [f"Eval: {passed}/{len(results)} passed", ""]
    for r in results:
        icon = "✓" if r["passed"] else "✗"
        lines.append(f"  {icon} {r['id']}")
        if not r["passed"]:
            for ar in r.get("assertions", []):
                if not ar["passed"]:
                    lines.append(f"     [{ar['type']}] {ar['reason']}")
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


def _tool_spec_recommend(args: dict[str, Any]) -> dict[str, Any]:
    """Recommend speculative decoding draft model."""
    import io as _io
    from contextlib import redirect_stdout
    target = args.get("target_model", "")
    if not target:
        return {"content": [{"type": "text", "text": "target_model required."}], "isError": True}
    from aictl.cmd.spec import run_recommend
    class FA:
        model = target
        all = False
        json = False
    buf = _io.StringIO()
    with redirect_stdout(buf):
        rc = run_recommend(FA())
    return {"content": [{"type": "text", "text": buf.getvalue().strip()}],
            "isError": rc != 0}


def _tool_fit(args: dict[str, Any]) -> dict[str, Any]:
    """Check VRAM fit at each quantization level."""
    model = args.get("model", "")
    gpu = args.get("gpu", "auto")
    if not model:
        return {"content": [{"type": "text", "text": "model required"}], "isError": True}
    from aictl.cmd.fit import _find_model, _calculate_quantizations, _lookup_gpu_vram
    from aictl.runtime.broker import full_detect
    from aictl.runtime.recommend import MODELS
    target = _find_model(model, MODELS)
    if not target:
        return {"content": [{"type": "text", "text": f"Unknown model: {model}"}], "isError": True}
    if gpu == "auto":
        hw = full_detect()
        gpu_name = hw.gpus[0].name if hw.gpus else "CPU"
        vram_mb = hw.gpus[0].vram_mb if hw.gpus else hw.system.ram_total_mb
    else:
        gpu_name, vram_mb = gpu, _lookup_gpu_vram(gpu)
    quants = _calculate_quantizations(target, 8192, 1)
    lines = [f"Model: {model}  GPU: {gpu_name} ({vram_mb//1024}GB)", ""]
    for n, d in quants.items():
        fits = "\u2713" if d["total_mb"] <= vram_mb * 0.9 else "\u2717"
        lines.append(f"  {n:<10} {d['total_mb']//1024:>3}GB  q={d['quality']*100:.0f}%  {fits}")
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


def _tool_quant(args: dict[str, Any]) -> dict[str, Any]:
    """Compare quantization formats."""
    model = args.get("model", "")
    if not model:
        return {"content": [{"type": "text", "text": "model required"}], "isError": True}
    use_case = args.get("use_case", "chat")
    from aictl.cmd.quant import QUANT_DATA
    qk = f"q_{use_case}"
    lines = [f"Quantization for {model} ({use_case})", ""]
    for n, d in QUANT_DATA.items():
        q = d.get(qk, d["q_chat"]) * 100
        lines.append(f"  {n:<10} quality={q:.0f}%  size={d['size']*100:.0f}%  "
                     f"speed={d['speed']:.1f}x  {','.join(d['engines'])}")
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


def _tool_guard_scan(args: dict[str, Any]) -> dict[str, Any]:
    """Scan for PII and content violations."""
    text = args.get("text", "")
    redact = args.get("redact", False)
    if not text:
        return {"content": [{"type": "text", "text": "text required"}], "isError": True}
    from aictl.core.guard import scan
    result, processed = scan(text, redact_pii=redact, block_on_injection=True)
    lines = []
    if result.pii:
        lines.append(f"PII ({len(result.pii)}):")
        for m in result.pii:
            lines.append(f"  [{m.kind}] {m.masked}")
    if result.violations:
        lines.append(f"Violations ({len(result.violations)}):")
        for v in result.violations:
            lines.append(f"  [{v.severity}] {v.rule}")
    if not result.pii and not result.violations:
        lines.append("Clean.")
    if redact and result.pii:
        lines.append(f"\nRedacted:\n{processed}")
    lines.append(f"\n{'BLOCKED' if not result.passed else 'PASSED'}")
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


def _tool_rag_ask(args: dict[str, Any]) -> dict[str, Any]:
    """Answer using indexed documents."""
    question = args.get("question", "")
    k = args.get("k", 5)
    if not question:
        return {"content": [{"type": "text", "text": "question required"}], "isError": True}
    from aictl.core.rag import RagStore, answer
    store = RagStore()
    if store.stats()["embedded"] == 0:
        return {"content": [{"type": "text", "text": "No documents indexed. Run: aictl rag index <path>"}]}
    response, sources = answer(question, store, k=k)
    lines = [response]
    if sources:
        lines.append(f"\nSources ({len(sources)}):")
        from pathlib import Path
        for c, s in sources:
            lines.append(f"  [{s:.3f}] {Path(c.source).name}")
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


def _tool_troubleshoot(args: dict[str, Any]) -> dict[str, Any]:
    """Diagnose inference problems."""
    import io as _io
    from contextlib import redirect_stdout
    symptom = args.get("symptom", "auto")
    from aictl.cmd.troubleshoot import (
        _detect_symptom_from_logs, _diagnose_oom, _diagnose_slow,
        _diagnose_wrong_output, _diagnose_cant_start, _diagnose_high_cost,
    )
    if symptom == "auto":
        symptom = _detect_symptom_from_logs()
        if not symptom:
            return {"content": [{"type": "text", "text": "No problems detected."}]}
    fns = {"oom": _diagnose_oom, "slow": _diagnose_slow,
           "wrong-output": _diagnose_wrong_output,
           "cant-start": _diagnose_cant_start, "high-cost": _diagnose_high_cost}
    fn = fns.get(symptom)
    if not fn:
        return {"content": [{"type": "text", "text": f"Unknown symptom: {symptom}"}], "isError": True}
    buf = _io.StringIO()
    with redirect_stdout(buf):
        fn()
    return {"content": [{"type": "text", "text": buf.getvalue().strip()}]}


def _tool_tco(args: dict[str, Any]) -> dict[str, Any]:
    """True cost of ownership + carbon/energy advisory."""
    import io as _io
    from contextlib import redirect_stdout
    from aictl.cmd.tco import run_summary, CARBON_INTENSITY_BY_REGION
    # Resolve carbon intensity: explicit value takes precedence over region shortcode.
    # Use 'in' check so that an explicit 0 is honoured rather than treated as falsy.
    ci = args["carbon_intensity"] if "carbon_intensity" in args else CARBON_INTENSITY_BY_REGION.get(
        args.get("region", "global"), 500)
    class _A:
        json = False
        period_days = args.get("period_days", 30)
        carbon_intensity = ci
        command = "tco"
    buf = _io.StringIO()
    with redirect_stdout(buf):
        run_summary(_A())
    return {"content": [{"type": "text", "text": buf.getvalue().strip()}]}


def _tool_guided(args: dict[str, Any]) -> dict[str, Any]:
    """Recommend a guided-decoding backend and/or validate against a schema."""
    from aictl.runtime.guided import (
        recommend_backend, vllm_flags, sglang_flags, ENGINE_BACKENDS,
        validate_json_schema,
    )
    engine = args.get("engine", "vllm").lower()
    out: dict[str, Any] = {}

    # Optional local JSON-Schema validation.
    if "schema" in args and "instance" in args:
        errors = validate_json_schema(args["instance"], args["schema"])
        out["validation"] = {"valid": not errors, "errors": errors}

    if engine not in ENGINE_BACKENDS:
        return {"content": [{"type": "text", "text": f"Unknown engine: {engine}"}],
                "isError": True}
    backend = recommend_backend(engine)
    if backend is None:
        out["engine"] = engine
        out["backend"] = None
        out["native"] = True
        out["note"] = "Ollama enforces JSON via its `format` field — no serve flag."
    else:
        flags = vllm_flags(backend) if engine in ("vllm", "tensorrt-llm") else sglang_flags(backend)
        out["engine"] = engine
        out["backend"] = backend
        out["serve_flags"] = flags
        out["alternatives"] = ENGINE_BACKENDS[engine][1:]
    return {"content": [{"type": "text", "text": json.dumps(out, indent=2)}]}


# ── JSON-RPC 2.0 Handler ──
def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    """Handle a single JSON-RPC 2.0 request."""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        return _response(req_id, {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    elif method == "initialized" or method.startswith("notifications/"):
        return None  # Notification, no response needed

    elif method == "tools/list":
        return _response(req_id, {"tools": TOOLS})

    elif method == "tools/call":
        name = params.get("name", "")
        if not name:
            # Missing tool name is a protocol error, not a tool error.
            return _error(req_id, -32602, "Invalid params: missing tool name")
        arguments = params.get("arguments", {})
        result = handle_tool(name, arguments)
        return _response(req_id, result)

    elif method == "ping":
        return _response(req_id, {})

    else:
        return _error(req_id, -32601, f"Method not found: {method}")


def _response(req_id: Any, result: Any) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 success response dict."""
    return {"jsonrpc": JSONRPC_VERSION, "id": req_id, "result": result}


def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 error response dict."""
    return {"jsonrpc": JSONRPC_VERSION, "id": req_id,
            "error": {"code": code, "message": message}}


# ── Main Loop (stdio transport) ──
def main() -> None:
    """Run MCP server on stdio."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError:
            err = _error(None, -32700, "Parse error")
            sys.stdout.write(json.dumps(err) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

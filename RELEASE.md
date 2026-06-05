# Release v1.5.0

## Highlights

- **676 tests** (659 Python + 17 Go), zero failures
- **24,552 lines** of code (22,610 Python + 1,942 Go)
- **Zero external Python dependencies** — stdlib only
- **46 Python + 29 Go CLI commands**
- **22 REST API endpoints**
- **10 MCP tools** for Claude Code/Cursor integration
- **7 K8s export formats** including llm-d ModelService

## What's New

### Tool Calling & Structured Output
Complete OpenAI-compatible tool calling + 5 structured output formats (guided_json, response_format, structured_outputs, Ollama format/schema).

### llm-d ModelService
Generate Helm values for the CNCF llm-d project with 3 presets (balanced/latency/throughput), LeaderWorkerSet for multi-GPU, and InferencePool integration.

### vLLM Optimization
Auto-generate optimal `vllm serve` flags: FP8 KV cache (2x compression), tensor parallelism, chunked prefill, prefix caching. GPU-aware (7 GPU profiles).

### Monitoring
Grafana dashboard (8 panels), Prometheus alerting rules (9 rules), OTel GenAI Semantic Conventions.

### NPU Detection
5 vendors: NVIDIA, Intel, AMD, Huawei Ascend (DeepSeek V4), Qualcomm Hexagon.

## Install

```bash
git clone https://github.com/shizukutanaka/aios.git
cd aios
python3 -m aictl demo --auto
```

## Requirements

- Python 3.11+
- Linux (any distro)
- Optional: Podman, NVIDIA GPU, Ollama

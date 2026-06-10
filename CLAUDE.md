# AI Native Linux OS (aios)

## What
`aictl` — CLI for local AI inference infrastructure on immutable Linux.
65 Python + 29 Go commands, 1755+ tests, zero external Python deps. v1.6.0.

## Map
```
aictl/cmd/        65 CLI commands (init doctor ps apply down recipe model ...)
aictl/core/       state config events audit apikeys cost security tenant metering
                  snapshots logging hooks plugins output constants
aictl/runtime/    broker adapters router autoscaler recommend mig fabric
                  continuity formats warmup benchmark dynamo isolation lora
                  speculative fallback optimize health nodes
aictl/stack/      manifest(10 recipes) orchestrator quadlet kserve gateway
                  disagg modelservice systemctl
aictl/daemon/     aiosd(22 REST) governor proxy mock_engine
aictl/trust/      verify cosign oras
aictl/metrics/    slo otel prometheus collector_config genai_spans
aictl/mcp_server  MCP server (18 tools, JSON-RPC 2.0 stdio)
go-port/          29 Go commands (Cobra)
tests/            98 test files, 1755+ tests
docs/             ADRs, OpenAPI, OPERATIONS, QUICKSTART
.claude/          3 agents, 5 commands, 8 skills
```

## Stack
- Python 3.11+ (stdlib only) + Go 1.23 (Cobra)
- bootc v1.15 (Fedora 42) · Podman + Quadlet · systemd
- vLLM v0.19 / SGLang v0.5 / Ollama v0.20
- K3s v1.35 + KServe v0.17 + llm-d v0.5 (CNCF)
- K8s Gateway API InferencePool v1
- NVIDIA Dynamo v0.8 (KVBM + NIXL)
- Cosign v3 · ORAS · OTel GenAI SemConv
- 7 K8s exports: KServe, Gateway API, KEDA, HPA, Dynamo, P/D Disagg, ModelService

## Rules
- NEVER add external Python deps — stdlib only
- Run `aictl gate` after ANY change
- All constants in `aictl/core/constants.py` — no hardcoded ports/versions
- All output supports `--json` flag
- Register new commands in `__main__.py` (import + register)
- vLLM metrics: `vllm:` prefix. SGLang: `sglang_` prefix
- Cosign v3: requires `--output json`
- Tests: `python3 -m unittest discover -s tests`
- Demo: `python3 -m aictl demo --auto` (no GPU needed)

## Workflows
```bash
# Verify everything works
aictl gate                         # Compile + import + version + 1755 tests + demo

# Add a new command
1. Create aictl/cmd/<name>.py      # register(sub) + run(args)
2. Import in __main__.py           # from aictl.cmd import <name>
3. Add <name>.register(sub)        # in build_parser()
4. Add tests/test_<name>.py
5. Run: aictl gate

# Deploy a model
aictl recommend                    # What fits your GPU?
aictl deploy optimize <model> --gpu H100  # Get vLLM flags
aictl deploy modelservice <model>  # llm-d Helm values
aictl deploy disagg <model>        # P/D split manifests
```

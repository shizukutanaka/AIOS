# AI OS Operations Runbook

## 1. Initial Deployment

```bash
# Install
curl -sSL https://raw.githubusercontent.com/shizukutanaka/aios/main/scripts/install.sh | bash

# Initialize + diagnose
aictl init
aictl doctor
aictl setup        # Interactive wizard

# Start daemon
aictl serve &      # Background, or:
sudo systemctl enable --now aiosd  # If installed as bootc image
```

## 2. Day-1: First AI Service

```bash
# CPU-only (no GPU)
aictl recipe run local-chat     # Ollama + Open WebUI → http://localhost:3000

# With GPU
aictl recipe run local-gpu-chat  # vLLM + Open WebUI
aictl recipe run code-assist     # Qwen2.5-Coder + Tabby
```

## 3. Daily Operations

### Check status
```bash
aictl status          # One-line dashboard
aictl ps              # Running stacks
aictl watch           # Continuous monitoring (Ctrl+C to exit)
aictl net             # Network diagnostics
```

### Model management
```bash
aictl recommend                    # Hardware-matched suggestions
aictl recommend --use-case code    # Filter by use case
aictl model list                   # Registered models
aictl model pull ghcr.io/org/model:v1  # Pull from OCI registry
aictl model verify ghcr.io/org/model:v1  # Cosign signature check
```

### Monitoring
```bash
aictl otel config > /etc/otel/config.yaml  # Generate OTel Collector config
# Prometheus: curl http://localhost:7700/metrics
# Grafana: Add data source → Prometheus → http://localhost:7700
```

## 4. Upgrades

### Pre-upgrade checklist
```bash
aictl context save        # Save KV cache state
aictl snapshot create --label pre-upgrade
aictl fabric detect       # Verify memory tiers
```

### Apply upgrade
```bash
# bootc-based upgrade (atomic, rollback-safe)
sudo bootc upgrade
# Or: sudo bootc switch ghcr.io/org/aios:v1.2.0

# After reboot:
aictl context restore     # Restore KV cache
aictl doctor              # Verify everything
```

### Rollback
```bash
sudo bootc rollback       # Switch to previous deployment
# Reboot
aictl snapshot restore <id>  # Restore state
```

## 5. Scaling to Cluster

### Phase 1: Add worker node
```bash
# On leader:
aictl node token

# On worker:
aictl node pair <leader-ip> --token <token>
```

### Phase 2: Promote to K3s
```bash
aictl cluster promote     # Generate plan
# Follow the steps output by the plan

# After K3s is running:
aictl cluster export team-rag > manifests.yaml
kubectl apply -f manifests.yaml
```

### Phase 3: KServe LLMInferenceService
```bash
aictl cluster export team-rag   # Generates LLMInferenceService CRD
# Includes: vLLM v0.19 --performance-mode, prefix caching, P/D disagg
kubectl apply -f -
```

## 6. Security

### API keys for proxy
```bash
aictl apikey create production --rpm 1000
# Save the key — cannot be displayed again
# Use: curl -H "Authorization: Bearer aios-..." http://localhost:8080/v1/chat/completions
```

### Model signing
```bash
# Sign with Cosign (keyless via OIDC)
cosign sign ghcr.io/org/model@sha256:...

# Verify in aictl
aictl model verify ghcr.io/org/model:latest
```

### Audit log
```bash
aictl audit -n 50
aictl audit --event proxy.request
# Logs: ~/.aios/audit/audit-YYYY-MM-DD.jsonl
```

## 7. GPU Management

### MIG partitioning (A100/H100/H200)
```bash
aictl mig plan --models llama3:16 embedding:2 whisper:4
# Apply the generated nvidia-smi commands
```

### Memory fabric
```bash
aictl fabric detect     # Show DRAM/CXL/NVMe tiers
aictl fabric policy     # Recommended data placement
```

## 8. Troubleshooting

| Symptom | Action |
|---------|--------|
| Engine unreachable | `aictl net` → check endpoint |
| High TTFT | `aictl watch` → check queue depth and KV cache |
| OOM kills | `aictl fabric detect` → check memory pressure |
| Stack won't start | `aictl logs <stack>` → check container errors |
| Proxy 429 errors | `aictl apikey list` → check rate limits |
| Upgrade broke things | `sudo bootc rollback` → reboot |

## 9. Configuration Reference

```bash
aictl config show            # Current config
aictl config set slo.ttft_p95_ms 500  # Set SLO target
aictl config set engines.vllm http://gpu-server:8000  # Remote engine
aictl config reset           # Restore defaults
```

### Key config paths
- `~/.aios/state.json` — Node state
- `~/.aios/config.json` — Persistent config
- `~/.aios/stacks.json` — Applied stacks
- `~/.aios/api_keys.json` — API keys
- `~/.aios/audit/` — Audit logs
- `/etc/containers/systemd/` — Quadlet units (root)

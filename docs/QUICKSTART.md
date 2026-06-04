# aictl Quickstart

Five minutes from zero to a working local AI.

## Install

```bash
git clone https://github.com/shizukutanaka/aios.git
cd aios
python3 -m aictl --version   # aictl 1.6.0
```

No pip install required for basic use. Zero external Python dependencies.

---

## Step 1: Check your hardware

```bash
aictl doctor
```

This tells you what GPU (if any) you have, how much VRAM, and what
models fit.

---

## Step 2: Find a model

```bash
aictl recommend
```

Shows models ranked by VRAM fit, quality, and use case.

To check a specific model before downloading:

```bash
aictl fit llama3:8b         # VRAM breakdown at each quantization level
aictl quant recommend llama3:8b  # Best quantization for your GPU
```

---

## Step 3: Install Ollama and run a model

```bash
# Install Ollama (takes ~30 seconds)
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &

# Pull and run a model
ollama pull qwen3:7b
```

Or use the guided setup:

```bash
aictl setup   # Walks you through everything interactively
```

---

## Step 4: Start using it

### From the CLI

```bash
aictl chat qwen3:7b          # Interactive chat
aictl dash                   # Full system dashboard
aictl status                 # Quick health check
aictl status --brief         # One-line status (for shell prompts)
```

### From Python

```python
import aictl

# Ask anything
answer = aictl.ai.ask("Summarize this", context=long_text)
print(answer)          # The response
print(answer.cost)     # "$0.000047" — per-call cost, always visible
print(answer.cached)   # True if semantic cache served this

# Classify text
label = aictl.ai.classify(
    "I love this product!",
    categories=["positive", "negative", "neutral"]
)

# Get embeddings
vectors = aictl.ai.embed(["doc1", "doc2"])

# Configure
aictl.ai.configure(cost_budget_usd=10.0)  # Monthly spending limit
```

---

## Key features at a glance

### RAG — search your own documents

```bash
aictl rag index ./docs        # Index Markdown, PDF, code files
aictl rag ask "What is our refund policy?"
aictl rag status              # Show index stats
```

### Guardrails — local PII and content filtering

```bash
aictl guard scan "Contact alice@example.com at 090-1234-5678"
# Detects: email, JP phone

aictl guard scan --redact "call me at 090-0000-0000"
# Returns: "call me at [REDACTED]"

aictl guard test              # Run built-in validation suite (7 tests)
```

### Cost tracking

```bash
aictl tco                     # Real cost: electricity + depreciation
aictl tco setup               # Configure GPU price and electricity rate
aictl cache status            # Semantic cache hit rate and tokens saved
```

### Team quotas

```bash
aictl quota create team-eng --tokens-per-month 10000000
aictl quota report            # Usage and cost per team
export AICTL_TEAM=team-eng    # SDK auto-tracks usage for this team
```

### Batch jobs

```bash
aictl batch add nightly-embed \
  --schedule "0 2 * * *" \
  --input ./docs \
  --task embed
aictl batch list              # View scheduled jobs
aictl batch run nightly-embed # Run immediately
```

### Troubleshooting

```bash
aictl troubleshoot            # Auto-detect symptom from logs
aictl troubleshoot --symptom oom  # CUDA OOM diagnosis
aictl troubleshoot --simulate llama3:70b  # "Would this model fit?"
```

---

## Explore further

```bash
aictl help                    # Guided overview
aictl help everyday           # Daily commands
aictl help models             # Working with models
aictl help cost               # Cost tracking
aictl help compliance         # Audit and regulation
aictl help advanced           # All 61 commands
```

# aictl — AI Native Linux OS

[![CI](https://github.com/shizukutanaka/aios/actions/workflows/ci.yml/badge.svg)](https://github.com/shizukutanaka/aios/actions)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Go 1.23](https://img.shields.io/badge/go-1.23-00ADD8.svg)](https://go.dev/)

ローカルAIインフラを「意識せず使える」形にする統合制御層。CLI でも、Python ライブラリとしても、同じことができます。

```python
import aictl

answer = aictl.ai.ask("Summarize this", context=long_text)
print(answer.cost)  # '$0.000047' — per-call cost, always visible
```

設定不要。モデル選定不要。aictl が自動で適切なモデルを選び、起動し、結果を返します。

```
1380 tests | 147 modules | 25,000+ lines | zero external Python deps
```

## What aictl does that competitors don't

| Feature | Ollama | LiteLLM | Portkey | vLLM | **aictl** |
|---------|--------|---------|---------|------|-----------|
| Hardware auto-detect | ✓ | ✗ | ✗ | ✗ | **✓** |
| VRAM fit check (`fit`) | ✗ | ✗ | ✗ | ✗ | **✓** |
| Quantization advisor (`quant`) | ✗ | ✗ | ✗ | ✗ | **✓** |
| Symptom diagnosis (`troubleshoot`) | ✗ | ✗ | ✗ | ✗ | **✓** |
| Local PII + guardrails (`guard`) | ✗ | ✗ | SaaS | ✗ | **✓ local** |
| Semantic cache (local) | ✗ | ✗ | SaaS | ✗ | **✓ local** |
| Per-call cost in every response | ✗ | aggregate | aggregate | ✗ | **✓** |
| Zero-config RAG (`rag index`) | ✗ | ✗ | ✗ | ✗ | **✓** |
| K8s export (7 formats) | ✗ | ✗ | ✗ | ✗ | **✓** |
| On-premise only | ✓ | ✓ | ✗ | ✓ | **✓** |

## Three ways to use it

**As a Python library** (most direct):
```python
import aictl
aictl.ai.ask("Is this email angry?", context=email_text)
aictl.ai.classify(text, categories=["bug", "feature", "question"])
aictl.ai.embed(["doc1", "doc2"])
```

**As a CLI** (for ops & exploration):
```bash
aictl                    # welcome screen, suggests next step
aictl doctor             # check the system
aictl recommend          # see models that fit your hardware
aictl fit llama3:70b     # will this specific model run?
aictl serve qwen3:7b     # actually run a model
```

**As Kubernetes manifests** (production deployment):
```bash
aictl deploy optimize llama3:70b --gpu H100   # generate vLLM serve flags
aictl cluster export --format kserve          # → KServe LLMInferenceService
aictl deploy modelservice qwen3:32b           # → llm-d Helm values
```

## Install

```bash
git clone https://github.com/shizukutanaka/aios.git
cd aios
python3 -m aictl --version   # aictl 1.6.0
```

## Quick Start

```bash
# 1. Check your hardware and pick a model
aictl doctor && aictl recommend

# 2. Index your documents and query them
aictl rag index ./docs
aictl rag ask "What is our refund policy?"

# 3. Scan text for PII before sharing
aictl guard scan "Contact alice@example.com at 090-1234-5678"

# 4. Compare two models before switching
aictl diff llama3.1:8b qwen3:7b --n 3

# 5. Route requests to the cheapest fitting model
aictl route ask "What is the capital of France?"   # → small model (SIMPLE)
aictl route ask "Explain quantum entanglement"      # → large model (COMPLEX)

# From Python:
import aictl
r = aictl.ai.ask("Summarize this", context=text)
print(r)           # the answer
print(r.cost)      # "$0.000047"
print(r.cached)    # True if semantic cache hit
```



```bash
python3 -m aictl init          # ハードウェア自動検出
python3 -m aictl demo --auto   # モックエンジンでフルスタック検証
python3 -m aictl recommend     # HW適合モデル推薦
```

## Features

| Feature | Description |
|---------|-------------|
| **推論エンジン** | vLLM v0.19 / SGLang v0.5 / Ollama v0.20 自動検出・ルーティング |
| **構造化出力** | guided_json, response_format, Ollama format, ツール呼び出し |
| **K8sエクスポート** | KServe, Gateway API, KEDA, HPA, Dynamo, P/D Disagg, ModelService (7形式) |
| **コスト最適化** | 7 GPU実勢価格比較, vLLMフラグ自動チューニング |
| **セキュリティ** | Cosign v3署名, セキュリティスキャナ, APIキー管理, 監査ログ |
| **MCP Server** | Claude Desktop/Cursor/VS Codeから直接操作 (16ツール) |
| **OS機能** | KVキャッシュ永続化, cgroup v2 OOM保護, メモリファブリック |
| **モニタリング** | OTel GenAI SemConv, Prometheus, Grafana ダッシュボード |
| **ローカルRAG** | `aictl rag index/ask/search` — SQLite、ゼロ外部依存 |
| **PII/コンテンツガード** | `aictl guard scan` — 9 PIIパターン、4ポリシー、完全ローカル |
| **真のTCO** | `aictl tco` — 電力費+減価償却+クラウド比較 |
| **スマートルーティング** | `aictl route` — 複雑度スコアでSIMPLE/MEDIUM/COMPLEXに自動分類 |
| **モデルA/B比較** | `aictl diff A B` — Jaccard類似度+レイテンシ+コスト |
| **プロンプト管理** | `aictl prompt save/get/history` — バージョン付き保存・エクスポート |
| **チームクォータ** | `aictl quota create/report` — チーム別トークン割当+チャージバック |
| **LLM評価** | `aictl eval run/compare` — CI統合可能なリグレッションテスト |

## Commands

```bash
# 基本
aictl init                             # ハードウェア検出 + 初期化
aictl doctor --deep                    # 詳細診断
aictl recommend                        # モデル推薦

# デプロイ
aictl deploy optimize <model> --gpu H100  # vLLMフラグ最適化
aictl deploy modelservice <model>         # llm-d Helm values
aictl deploy disagg <model>               # P/D分離デプロイ

# 運用
aictl cost compare                     # GPU価格比較
aictl security                         # セキュリティスキャン
aictl bench --mock -n 10               # ベンチマーク
aictl meter usage                      # トークン使用量
aictl lora add <name> --base <model>   # LoRAアダプタ

# v1.6.0 — AI品質・コスト管理
aictl diff llama3.1:8b qwen3:7b        # モデルA/B比較
aictl route show "your question"       # 複雑度スコア→最適モデル選択
aictl route ask  "your question"       # スマートルーティングで回答
aictl prompt save --name myp --text "Summarize: {input}"  # プロンプト保存
aictl eval run --suite ./tests.json    # LLMリグレッションテスト
aictl guard scan "text with email"     # PII検出
aictl tco                              # 今月の真のAIコスト
aictl quota create team-eng --tokens-per-month 10000000

# K8s
aictl cluster gateway <stack>          # Gateway API InferencePool
aictl scale keda <stack>               # KEDA ScaledObject
```

## Architecture

```
CLI (65 Python + 29 Go)
├── Runtime   Broker → Router → AutoScaler → Fabric → Isolation
├── Daemon    22 REST API + Proxy + SLO Governor + Mock Engine
├── Stack     10 Recipes + Quadlet + KServe + Gateway API + llm-d
├── Core      Metering + Security + Cost + API Keys + Audit
├── Trust     Cosign v3 + ORAS
├── Metrics   OTel GenAI SemConv + Prometheus
├── MCP       18 tools (stdio JSON-RPC 2.0)
└── v1.6.0    RAG + Guard + TCO + Quota + Batch + Eval + Diff + Prompt + Route
```

## Technology Stack

| Layer | Technology |
|-------|-----------|
| OS | bootc v1.15 (Fedora 42, immutable) |
| Container | Podman + Quadlet (systemd) |
| Inference | vLLM v0.19 / SGLang v0.5 / Ollama v0.20 |
| K8s | K3s v1.35 + KServe v0.17 + llm-d v0.5 (CNCF) |
| GPU | NVIDIA Dynamo v0.8 (KVBM + NIXL) |
| NPU | NVIDIA, Intel, AMD, Huawei Ascend, Qualcomm |
| Signing | Cosign v3 + ORAS |
| Monitoring | OTel GenAI SemConv + Prometheus + Grafana |

## Docker Quick Start

```bash
docker compose up -d
curl http://localhost:7700/v1/health   # Daemon API
curl http://localhost:9999/v1/models   # Mock engine
```

## MCP Server

```json
{
  "mcpServers": {
    "aictl": {
      "command": "python3",
      "args": ["-m", "aictl.mcp_server"]
    }
  }
}
```

## Testing

```bash
make test       # 1380 Python tests
make go-test    # 17 Go tests
make gate       # Full quality gate
make demo       # E2E demo
```

## Requirements

- Python 3.11+ (stdlib only, zero deps)
- Linux (any distro)
- Optional: Podman, NVIDIA GPU, Ollama

## License

MIT

## MCP Server — use aictl from Claude Desktop, Cursor, VS Code

aictl exposes 18 tools via the Model Context Protocol (MCP). Any MCP-compatible host can use them (the table below lists a representative selection).

```json
// Claude Desktop config (~/.config/claude/mcp_servers.json):
{
  "aictl": {
    "command": "python3",
    "args": ["-m", "aictl.mcp_server"]
  }
}
```

Then in Claude Desktop, ask naturally:

> "Will llama3:70b fit on my RTX 4090?"
> "Scan this text for PII: alice@example.com called 090-1234-5678"
> "What's the cheapest quantization for qwen3:7b that keeps 95%+ quality?"
> "What's my true AI cost this month?"

### Available MCP tools

| Tool | Description |
|------|------------|
| `aictl_fit` | Check if a model fits your GPU at each quantization level |
| `aictl_quant` | Compare quantization formats (quality/size/speed) |
| `aictl_guard_scan` | Scan text for PII and policy violations (local only) |
| `aictl_rag_ask` | Answer questions from indexed documents |
| `aictl_troubleshoot` | Diagnose inference problems by symptom |
| `aictl_tco` | True Cost of Ownership (electricity + depreciation) |
| `aictl_recommend` | Hardware-aware model recommendations |
| `aictl_health` | System health check |
| `aictl_status` | One-line system status |
| `aictl_optimize` | Generate optimal vLLM serve flags |

Zero external dependencies. Stdlib-only JSON-RPC 2.0 over stdio.

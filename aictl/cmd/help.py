"""aictl help — guided discovery, not a wall of options.

Apple principle: show 5 things you actually do, not 65 things you
*could* do. Progressive disclosure: power users find more by typing
`aictl help <topic>`.
"""

from __future__ import annotations

from typing import Any

import argparse


TOPICS: dict[str, str] = {
    "getting-started": """\
aictl — Getting Started

The shortest path from zero to a working local AI:

  1. Check your hardware:
       aictl doctor

  2. Pick a model that fits:
       aictl recommend

  3. Try it (no real GPU needed):
       aictl demo --auto

That's the whole onboarding. Three commands, three minutes.

Next, explore:
  aictl help everyday      Daily commands
  aictl help models        Working with models
  aictl help cost          Track what you're spending
  aictl help quality       Eval, diff, routing
  aictl help compliance    Regulatory reporting
  aictl help advanced      The full 65-command surface
""",

    "everyday": """\
aictl — Everyday Commands

  aictl doctor             Check the system
  aictl recommend          See models that fit your hardware
  aictl fit <model>        Will this specific model run?
  aictl quant <model>      Pick the right quantization
  aictl serve <model>      Start a model
  aictl ps                 List what's running
  aictl down               Stop a stack
  aictl troubleshoot       Symptom-based problem diagnosis
  aictl dash               Full system dashboard (one screen)
  aictl guard scan <text>  Scan for PII and policy violations
  aictl tco                Your real AI cost this month
  aictl route show <text>  Which model should handle this?

These twelve cover ~95% of daily use.
""",

    "models": """\
aictl — Working with Models

  aictl recommend                 List models for your GPU
  aictl model pull <name>         Download a model
  aictl model list                Show local models
  aictl model verify <name>       Check Cosign signature
  aictl fit <name>                Will it fit on my GPU?
  aictl quant compare <name>      Quality vs size for each format
  aictl quant recommend <name>    One specific recommendation
  aictl spec recommend <name>     Best draft model for speculative decoding
  aictl spec --all                Full speculative decoding pairing table
""",

    "cost": """\
aictl — Cost & Usage

  aictl cost                      Compare GPU/cloud price options
  aictl meter                     Token usage by tenant/key
  aictl tco                       True cost (electricity + depreciation)
  aictl tco setup                 Configure GPU price and electricity rate
  aictl quota create <team>       Allocate tokens per team
  aictl quota report              Monthly usage summary
  aictl cache status              Semantic cache hit rate and savings
  aictl route show <text>         Route to cheapest model that fits the task
  aictl batch add <job>           Queue low-priority work for GPU idle time
""",

    "quality": """\
aictl — Eval, Diff, RAG & Smart Routing

Document search (RAG):
  aictl rag index ./docs              Index Markdown, PDF, code files
  aictl rag ask "your question"       Answer from indexed documents
  aictl rag search "query"            See raw matching chunks
  aictl rag status                    Index stats
  aictl rag reset                     Clear the index

Model evaluation:
  aictl eval create --suite ./my.json     Create a test suite template
  aictl eval run    --suite ./my.json     Run all test cases
  aictl eval compare --suite ./my.json \\
                     --baseline ./b.json  Detect regressions
  aictl eval report  --suite ./my.json    Human-readable results

Model comparison:
  aictl diff llama3.1:8b qwen3:7b         Compare outputs on 5 prompts
  aictl diff A B --prompts ./tests.json   Compare on your own prompts
  aictl diff A B --n 3 --json             Machine-readable results

Prompt management:
  aictl prompt save --name summarize \\
                    --text "Summarize: {input}"
  aictl prompt list
  aictl prompt get summarize
  aictl prompt history summarize          See all versions
  aictl prompt run --name summarize \\
                   --input "your text"
  aictl prompt export --name summarize \\
                       --format eval > suite.json

Smart routing (cost optimization):
  aictl route show "your question"        Score + which model
  aictl route ask  "your question"        Route and answer
  aictl route config                      Set model tiers
  aictl route test --n 12                 Benchmark routing accuracy
  aictl route batch --file prompts.json   Classify a batch
""",

    "compliance": """\
aictl — Regulatory Compliance

  aictl audit                     Recent audit events
  aictl trace                     OpenTelemetry traces
  aictl security                  Security posture check

Reporting:
  aictl report --format eu        EU AI Act technical documentation
  aictl report --format nist      NIST AI RMF
  aictl report --format jp        JP AI Guidelines
  aictl report --format soc2      SOC 2 evidence

All reports are signed with Cosign v3 and independently verifiable.
""",

    "kubernetes": """\
aictl — Kubernetes Deployment

  aictl deploy optimize <model>            Generate vLLM serve flags
  aictl deploy modelservice <model>        llm-d Helm values
  aictl deploy disagg <model>              P/D disaggregated config

Cluster operations:
  aictl cluster export                     Export to KServe/Gateway API
  aictl scale                              HPA/KEDA scaling rules
  aictl fabric                             Multi-node fabric (NVLink/NIXL)
""",

    "advanced": """\
aictl — Advanced (Power User Mode)

For the full 65-command surface:

  AICTL_EXPERT=1 aictl --help

Or query a specific command directly:

  aictl <command> --help

All commands by category:
  Lifecycle:      init, setup, upgrade, snapshot, gate, selftest, health, info, update
  Cluster:        cluster, node, scale, fabric, mig, net
  Deployment:     apply, deploy, recipe, image, convert
  Observability:  watch, logs, log, otel, trace, audit, bench, perf
  Tenancy:        tenant, apikey, meter, security
  Inference:      proxy, warmup, spec, lora, context, chat, completion
  Cost:           tco, quota, batch, cache, cost
  AI Safety:      guard, route
  RAG:            rag
  Quality:        eval, diff, prompt
""",
}


def register(sub: Any) -> None:
    """Register CLI subcommand."""
    p = sub.add_parser(
        "help",
        help="Show what aictl can do (guided tour).",
        description=(
            "Discovery-oriented help. `aictl help` shows the starting point. "
            "`aictl help <topic>` drills in. `aictl <command> --help` shows a "
            "specific command's options."
        ),
    )
    p.add_argument(
        "topic",
        nargs="?",
        help=f"One of: {', '.join(TOPICS.keys())}, or any command name.",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Show topic-based help or per-command --help."""
    topic = getattr(args, "topic", None)

    if not topic:
        print(TOPICS["getting-started"])
        return 0

    if topic in TOPICS:
        print(TOPICS[topic])
        return 0

    # Maybe a command name
    try:
        from aictl.__main__ import build_parser
        parser = build_parser()
        for action in parser._actions:
            if hasattr(action, "choices") and action.choices and topic in action.choices:
                action.choices[topic].print_help()
                return 0
    except Exception:
        pass  # best-effort; failure is non-critical

    print(f"Unknown topic: {topic}")
    print()
    print("Available topics:")
    for t in TOPICS:
        print(f"  aictl help {t}")
    print()
    print("Or: aictl <command> --help")
    return 1

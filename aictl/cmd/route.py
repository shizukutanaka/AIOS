"""aictl route — Complexity-aware request routing.

LiteLLM charges $19/month for this. aictl does it locally, zero deps.

Problem: Using a 70B model for "What is 2+2?" wastes 100x cost.
Solution: Route simple requests to a fast small model, complex ones to
          a powerful large model.

Complexity scoring (heuristics, all local):
  word_count     — longer = more complex
  question_words — "why", "explain", "how does" = complex
  tech_terms     — code/math/reasoning markers
  jargon         — domain-specific vocabulary
  sentence_count — multi-sentence = more context required

Score 0-100:
  0-30:   SIMPLE   → fast small model (llama3.2:1b, qwen3:0.5b)
  31-60:  MEDIUM   → balanced model  (qwen3:7b, llama3.1:8b)
  61-100: COMPLEX  → powerful model  (qwen3:32b, llama3.1:70b)

Usage:
  aictl route show "What is 2+2?"          # score + which model
  aictl route show "Explain quantum entanglement and its applications"
  aictl route ask "your question"          # score then answer with right model
  aictl route config                       # show/edit model tiers
  aictl route test --n 10                  # benchmark routing accuracy
"""

from __future__ import annotations

import argparse

from typing import Any

import json
import os
import re
import time
from pathlib import Path

from aictl.core.output import ok, warn, print_json


# ── Complexity heuristics ─────────────────────────────────

_COMPLEX_PATTERNS = [
    r'\bwhy\b', r'\bexplain\b', r'\bhow does\b', r'\bwhat causes\b',
    r'\bcompare\b', r'\banalyze\b', r'\bcritique\b', r'\bevaluate\b',
    r'\bimplications\b', r'\bphilosophy\b', r'\bethics\b', r'\btheory\b',
    r'\bpros and cons\b', r'\badvantages.*disadvantages\b', r'\btrade.?offs?\b',
    r'\boptimize\b', r'\boptimisation\b', r'\bperformance\b.*\bhow\b',
]
_CODE_PATTERNS = [
    r'\bimport\b', r'\bdef \b', r'\bfunction\b', r'\balgorithm\b',
    r'\bcomplexity\b', r'\bO\(n', r'\bbig-O\b', r'\bdebug\b',
    r'```', r'\bclass \b', r'\bSQL\b', r'\bregex\b',
    r'\bquery\b', r'\bindex\b', r'\bdatabase\b', r'\bDocker\b',
    r'\bKubernetes\b', r'\bmicroservices?\b', r'\bAPI\b',
]
_SIMPLE_PATTERNS = [
    r'^what is\b', r'^who is\b', r'^when is\b', r'^where is\b',
    r'^list \d', r'^give me \d', r'^name \d',
]


def score_complexity(text: str) -> int:
    """Return complexity score 0–100.

    Higher = more complex, warrants a larger model.
    """
    s = 0
    lower = text.lower()

    # Length contribution (0-30 points) — calibrated: simple ≤ 8 words
    words = len(text.split())
    s += min(30, words * 3)

    # Complex question patterns (up to 40 points)
    for pat in _COMPLEX_PATTERNS:
        if re.search(pat, lower):
            s += 12
    s = min(s, 70)

    # Code/technical markers (up to 20 points)
    for pat in _CODE_PATTERNS:
        if re.search(pat, lower):
            s += 8
    s = min(s, 80)

    # Multiple sentences = more context (up to 10 points)
    sentences = len(re.split(r'[.!?]+', text.strip())) - 1
    s += min(10, sentences * 4)

    # Short comparison/contrast or design questions = COMPLEX
    if re.search(r'\bcompare\b|\bversus\b|\bvs\b|\bdesign\b|\barchitect', lower):
        s = max(s, 62)

    # "implications" always means complex analysis
    if re.search(r'\bimplications\b|\bconsequences\b|\btradeoffs?\b', lower):
        s = max(s, 65)

    # Simple question patterns (reduce score strongly)
    for pat in _SIMPLE_PATTERNS:
        if re.search(pat, lower):
            s = max(0, s - 30)
            break

    return min(100, s)


def classify_complexity(score: int) -> str:
    """Return SIMPLE | MEDIUM | COMPLEX."""
    if score <= 30:
        return "SIMPLE"
    if score <= 60:
        return "MEDIUM"
    return "COMPLEX"


# ── Default tier configuration ────────────────────────────

_DEFAULT_TIERS = {
    "simple":  {"model": "llama3.2:1b",  "max_score": 30},
    "medium":  {"model": "qwen3:7b",     "max_score": 60},
    "complex": {"model": "qwen3:32b",    "max_score": 100},
}


def register(sub: Any) -> None:
    """Register CLI subcommand."""
    p = sub.add_parser(
        "route",
        help="Smart routing: match request complexity to the right model. Saves cost.",
    )
    sp = p.add_subparsers(dest="route_cmd", required=True)

    # show
    sh = sp.add_parser("show", help="Score a prompt and show which model it routes to.")
    sh.add_argument("prompt", help="The prompt to analyze")
    sh.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    sh.set_defaults(func=run_show)

    # ask
    a = sp.add_parser("ask", help="Route and answer a prompt with the optimal model.")
    a.add_argument("prompt", help="The prompt to answer")
    a.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    a.set_defaults(func=run_ask)

    # config
    c = sp.add_parser("config", help="Show or update model tier configuration.")
    c.add_argument("--simple",  help="Model for SIMPLE queries (score 0-30)")
    c.add_argument("--medium",  help="Model for MEDIUM queries (score 31-60)")
    c.add_argument("--complex", help="Model for COMPLEX queries (score 61-100)")
    c.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    c.set_defaults(func=run_config)

    # test
    t = sp.add_parser("test", help="Run routing accuracy benchmark on built-in test set.")
    t.add_argument("--n",    type=int, default=10, help="Number of test prompts")
    t.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    t.set_defaults(func=run_test)

    # batch
    b = sp.add_parser("batch", help="Route a batch of prompts from JSON file.")
    b.add_argument("--file", required=True, help="JSON file with prompt list")
    b.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    b.set_defaults(func=run_batch)


def run_show(args: argparse.Namespace) -> int:
    """Show routing decision for a prompt."""
    prompt = args.prompt
    score = score_complexity(prompt)
    tier = classify_complexity(score)
    cfg = _load_config()
    model = cfg[tier.lower()]["model"]

    if getattr(args, "json", False):
        print_json({
            "prompt": prompt[:100],
            "score": score,
            "tier": tier,
            "model": model,
        })
        return 0

    print()
    bar = "█" * (score // 5) + "░" * (20 - score // 5)
    print(f"  Complexity: [{bar}] {score}/100  →  {tier}")
    print(f"  Routes to:  {model}")
    print()

    # Why?
    reasons = _explain_score(prompt)
    if reasons:
        print("  Signals:")
        for r in reasons:
            print(f"    {r}")
        print()
    return 0


def run_ask(args: argparse.Namespace) -> int:
    """Route a prompt and answer it with the optimal model."""
    prompt = args.prompt
    score = score_complexity(prompt)
    tier = classify_complexity(score)
    cfg = _load_config()
    model = cfg[tier.lower()]["model"]

    use_json = getattr(args, "json", False)
    if not use_json:
        print()
        ok(f"Routing to {model} (score={score}, tier={tier})")
        print()

    try:
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()
        import aictl
        t0 = time.perf_counter()
        r = aictl.ai.ask(prompt, model=model)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        if use_json:
            print_json({
                "prompt": prompt,
                "score": score,
                "tier": tier,
                "model": model,
                "response": str(r),
                "cost": r.cost,
                "latency_ms": elapsed_ms,
            })
        else:
            print(str(r))
            print()
            print(f"  Cost: {r.cost}  Latency: {elapsed_ms}ms")
            print()
    except Exception as e:
        warn(f"Inference failed: {e}")
    return 0


def run_config(args: argparse.Namespace) -> int:
    """Show or update tier configuration."""
    cfg = _load_config()

    # Apply updates
    updated = False
    for tier in ["simple", "medium", "complex"]:
        val = getattr(args, tier, None)
        if val:
            cfg[tier]["model"] = val
            updated = True

    if updated:
        _save_config(cfg)
        ok("Route configuration updated.")
        print()

    if getattr(args, "json", False):
        print_json(cfg)
        return 0

    print()
    print("  Model tiers:")
    print(f"    SIMPLE  (score 0-30):   {cfg['simple']['model']}")
    print(f"    MEDIUM  (score 31-60):  {cfg['medium']['model']}")
    print(f"    COMPLEX (score 61-100): {cfg['complex']['model']}")
    print()
    print("  Update: aictl route config --simple llama3.2:1b --complex qwen3:32b")
    print()
    return 0


def run_test(args: argparse.Namespace) -> int:
    """Run accuracy benchmark on built-in test set."""
    # Labeled prompts: (expected_tier, prompt)
    _TEST_CASES = [
        ("SIMPLE",  "What is 2+2?"),
        ("SIMPLE",  "Who is the current US president?"),
        ("SIMPLE",  "What is the capital of France?"),
        ("SIMPLE",  "Give me 3 colors."),
        ("MEDIUM",  "Write a Python function that sorts a list."),
        ("MEDIUM",  "Explain how TCP/IP works in 3 sentences."),
        ("MEDIUM",  "What are the pros and cons of Docker?"),
        ("MEDIUM",  "How do I optimize a slow SQL query?"),
        ("COMPLEX", "Explain quantum entanglement and its implications for computing."),
        ("COMPLEX", "Compare Kant's categorical imperative with utilitarianism."),
        ("COMPLEX", "Why does speculative decoding improve LLM throughput? Explain the math."),
        ("COMPLEX", "Design a distributed cache system that handles 1M requests/second."),
    ]

    n = min(getattr(args, "n", 10), len(_TEST_CASES))
    cases = _TEST_CASES[:n]
    correct = 0
    results = []

    for expected, prompt in cases:
        score = score_complexity(prompt)
        predicted = classify_complexity(score)
        match = predicted == expected
        if match:
            correct += 1
        results.append({
            "prompt": prompt[:60],
            "expected": expected,
            "predicted": predicted,
            "score": score,
            "correct": match,
        })

    accuracy = correct / max(len(cases), 1) * 100

    if getattr(args, "json", False):
        print_json({"cases": results, "accuracy_pct": round(accuracy, 1)})
        return 0

    print()
    print(f"  Routing accuracy test ({len(cases)} prompts)")
    print()
    for r in results:
        icon = "✓" if r["correct"] else "✗"
        print(f"  {icon} [{r['score']:>3}] {r['expected']:<8} → {r['predicted']:<8}  {r['prompt']}")
    print()
    ok(f"Accuracy: {correct}/{len(cases)} ({accuracy:.0f}%)")
    print()
    return 0


def run_batch(args: argparse.Namespace) -> int:
    """Route a batch of prompts."""
    try:
        raw = json.loads(Path(args.file).read_text())
    except Exception as e:
        from aictl.core.output import err
        err(f"Cannot read file: {e}")
        return 1

    if not isinstance(raw, list):
        from aictl.core.output import err
        err("File must be a JSON array of strings.")
        return 1

    cfg = _load_config()
    results = []
    tier_counts: dict[str, int] = {}

    for prompt in raw:
        if not isinstance(prompt, str):
            continue
        score = score_complexity(prompt)
        tier = classify_complexity(score)
        model = cfg[tier.lower()]["model"]
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        results.append({"prompt": prompt[:80], "score": score, "tier": tier, "model": model})

    if getattr(args, "json", False):
        print_json({"results": results, "tier_counts": tier_counts, "total": len(results)})
        return 0

    print()
    print(f"  Batch routing: {len(results)} prompts")
    for tier, count in sorted(tier_counts.items()):
        model = cfg[tier.lower()]["model"]
        print(f"    {tier:<8} {count:>3} prompts → {model}")
    print()
    return 0


# ── Helpers ───────────────────────────────────────────────

def _explain_score(text: str) -> list[str]:
    """Return human-readable reasons for the score."""
    reasons = []
    lower = text.lower()
    words = len(text.split())
    if words > 20:
        reasons.append(f"Long prompt ({words} words)")
    for pat in _COMPLEX_PATTERNS:
        if re.search(pat, lower):
            keyword = pat.replace(r"\b", "").strip()
            reasons.append(f"Complex keyword: '{keyword}'")
            if len(reasons) >= 3:
                break
    for pat in _CODE_PATTERNS:
        if re.search(pat, lower):
            reasons.append("Code/technical content")
            break
    return reasons


def _config_path() -> Path:
    """Return the path to the TCO configuration file."""
    base = os.environ.get("AIOS_STATE_DIR", os.path.expanduser("~/.aios"))
    return Path(base) / "route_config.json"


def _load_config() -> dict[str, Any]:
    """Load data from persistent storage."""
    path = _config_path()
    if path.exists():
        try:
            cfg = json.loads(path.read_text())
            for tier, defaults in _DEFAULT_TIERS.items():
                cfg.setdefault(tier, defaults)
            return cfg
        except Exception:
            pass  # best-effort; failure is non-critical
    return dict(_DEFAULT_TIERS)


def _save_config(cfg: dict[str, Any]) -> None:
    """Persist data to storage."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))

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

import hashlib
import heapq
import json
import os
import re
import threading
import time
from pathlib import Path

from aictl.core.output import ok, warn, err, print_json
from aictl.core.argtypes import positive_int
from aictl.core.atomicio import atomic_write_text


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


# Labeled prompts (expected_tier, prompt) for `route test`'s accuracy
# benchmark. Hoisted to module scope (was function-local inside run_test)
# so it can also be referenced by tests asserting disjointness from
# _KNN_EXAMPLES below -- same 12 entries, unchanged.
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

# Labeled examples for the embedding-kNN router (IMPROVEMENTS.md item C-1),
# disjoint from _TEST_CASES (so `route test --knn`'s accuracy numbers are an
# honest out-of-sample measurement, not the kNN bank grading itself).
# ~10 per tier, weighted toward the SIMPLE/MEDIUM and MEDIUM/COMPLEX
# boundaries (score 25-35 / 55-65) since that's exactly where the gate in
# route_tier_gated() actually consults kNN -- prompts far from a boundary
# never reach the kNN vote at all, so bank density there matters less.
EXPECTED_KNN_EXAMPLES = 30
_KNN_EXAMPLES: list[tuple[str, str]] = [
    ("SIMPLE",  "What time is it in Tokyo?"),
    ("SIMPLE",  "Name 5 fruits."),
    ("SIMPLE",  "What is the boiling point of water?"),
    ("SIMPLE",  "Who wrote Romeo and Juliet?"),
    ("SIMPLE",  "Convert 10 miles to kilometers."),
    ("SIMPLE",  "What year did World War II end?"),
    ("SIMPLE",  "List 3 programming languages."),
    ("SIMPLE",  "What is the largest planet?"),
    ("SIMPLE",  "Summarize this in one word: happy."),
    ("SIMPLE",  "Translate 'hello' to Spanish."),
    ("MEDIUM",  "Write a function to reverse a string in Python."),
    ("MEDIUM",  "Summarize the plot of a typical mystery novel."),
    ("MEDIUM",  "What's the difference between a list and a tuple?"),
    ("MEDIUM",  "Draft a short email declining a meeting."),
    ("MEDIUM",  "Explain what an API is to a beginner."),
    ("MEDIUM",  "How do I set up a virtual environment in Python?"),
    ("MEDIUM",  "What are three tips for better sleep?"),
    ("MEDIUM",  "Write a SQL query to count rows in a table."),
    ("MEDIUM",  "Explain the difference between HTTP and HTTPS."),
    ("MEDIUM",  "What's a good approach to learning a new language?"),
    ("COMPLEX", "Analyze the trade-offs between microservices and a monolith for a startup."),
    ("COMPLEX", "Critique the ethical implications of autonomous weapons systems."),
    ("COMPLEX", "Design a rate limiter that scales across multiple data centers."),
    ("COMPLEX", "Compare the philosophy of Stoicism and Existentialism on free will."),
    ("COMPLEX", "Explain the mathematical intuition behind backpropagation in neural networks."),
    ("COMPLEX", "Evaluate the long-term economic consequences of universal basic income."),
    ("COMPLEX", "Architect a fault-tolerant event-sourcing system for financial transactions."),
    ("COMPLEX", "Discuss the trade-offs between consistency and availability in distributed databases."),
    ("COMPLEX", "Analyze why transformer attention scales quadratically and how to mitigate it."),
    ("COMPLEX", "Compare the performance implications of optimistic versus pessimistic locking."),
]


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
    sh.add_argument(
        "--knn", action="store_true",
        help="Consult the embedding-kNN tie-breaker even if route_knn_enabled is off in config.",
    )
    sh.set_defaults(func=run_show)

    # ask
    a = sp.add_parser("ask", help="Route and answer a prompt with the optimal model.")
    a.add_argument("prompt", help="The prompt to answer")
    a.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    a.add_argument(
        "--knn", action="store_true",
        help="Consult the embedding-kNN tie-breaker even if route_knn_enabled is off in config.",
    )
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
    t.add_argument("--n",    type=positive_int, default=10, help="Number of test prompts")
    t.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    t.add_argument(
        "--knn", action="store_true",
        help="Consult the embedding-kNN tie-breaker even if route_knn_enabled is off in config.",
    )
    t.set_defaults(func=run_test)

    # batch
    b = sp.add_parser("batch", help="Route a batch of prompts from JSON file.")
    b.add_argument("--file", required=True, help="JSON file with prompt list")
    b.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    b.set_defaults(func=run_batch)

    # cascade
    cas = sp.add_parser(
        "cascade",
        help="Try simple model first; escalate to complex model if quality is insufficient.",
    )
    cas.add_argument("prompt", help="The prompt to answer")
    cas.add_argument(
        "--min-length", type=int, default=20,
        help="Minimum response word count to accept (default: 20)",
    )
    cas.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    cas.set_defaults(func=run_cascade)

    # stats
    st = sp.add_parser("stats", help="Show cascade routing statistics.")
    st.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    st.set_defaults(func=run_stats)


def run_show(args: argparse.Namespace) -> int:
    """Show routing decision for a prompt."""
    prompt = args.prompt
    tier, meta = route_tier_gated(prompt, force=getattr(args, "knn", False))
    score = meta["score"]
    cfg = _load_config()
    model = cfg[tier.lower()]["model"]

    if getattr(args, "json", False):
        print_json({
            "prompt": prompt[:100],
            "score": score,
            "tier": tier,
            "model": model,
            "knn_applied": meta["knn_applied"],
        })
        return 0

    print()
    bar = "█" * (score // 5) + "░" * (20 - score // 5)
    print(f"  Complexity: [{bar}] {score}/100  →  {tier}")
    print(f"  Routes to:  {model}")
    if meta["knn_applied"]:
        print(f"  kNN tie-break applied (regex tier was {meta['regex_tier']})")
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
    tier, meta = route_tier_gated(prompt, force=getattr(args, "knn", False))
    score = meta["score"]
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
                "knn_applied": meta["knn_applied"],
            })
        else:
            print(str(r))
            print()
            print(f"  Cost: {r.cost}  Latency: {elapsed_ms}ms")
            print()
    except Exception as e:
        warn(f"Inference failed: {e}")
        return 1
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
    use_knn = getattr(args, "knn", False)

    # --n is a count of test prompts; reject < 1 before the negative-slice trap
    # (`_TEST_CASES[:n]` with n=-3 runs all-but-last-3 cases, more than asked).
    raw_n = getattr(args, "n", 10)
    if raw_n < 1:
        err(f"--n must be >= 1 (got {raw_n}).")
        return 1
    n = min(raw_n, len(_TEST_CASES))
    cases = _TEST_CASES[:n]
    correct = 0
    results = []

    for expected, prompt in cases:
        predicted, meta = route_tier_gated(prompt, force=use_knn)
        score = meta["score"]
        match = predicted == expected
        if match:
            correct += 1
        results.append({
            "prompt": prompt[:60],
            "expected": expected,
            "predicted": predicted,
            "score": score,
            "correct": match,
            "knn_applied": meta["knn_applied"],
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
        knn_tag = " [knn]" if r["knn_applied"] else ""
        print(f"  {icon} [{r['score']:>3}] {r['expected']:<8} → {r['predicted']:<8}{knn_tag}  {r['prompt']}")
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


def run_cascade(args: argparse.Namespace) -> int:
    """Cascade routing: cheap model first, escalate to powerful model on low quality.

    Algorithm:
      1. Score the prompt — if already COMPLEX, skip cascade (go direct to complex).
      2. Try the SIMPLE model.
      3. Check response quality heuristic (length + uncertainty phrases).
      4. If quality is insufficient, escalate to the COMPLEX model.
      5. Report which path was taken and both costs.
    """
    prompt = args.prompt
    min_length = max(1, getattr(args, "min_length", 20))
    use_json = getattr(args, "json", False)

    score = score_complexity(prompt)
    tier = classify_complexity(score)
    cfg = _load_config()

    # For COMPLEX prompts, cascade doesn't save cost — go direct.
    if tier == "COMPLEX":
        model = cfg["complex"]["model"]
        escalated = False
        first_model = model
        first_response = None
        try:
            from aictl.sdk import _AmbientContext
            _AmbientContext.reset_for_testing()
            import aictl
            t0 = time.perf_counter()
            r = aictl.ai.ask(prompt, model=model)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            response_text = str(r)
            total_cost = r.cost
        except Exception as e:
            warn(f"Inference failed: {e}")
            return 1
    else:
        # Step 1: try simple model
        first_model = cfg["simple"]["model"]
        escalated = False
        first_response = None
        total_cost = 0.0
        response_text = ""
        elapsed_ms = 0

        try:
            from aictl.sdk import _AmbientContext
            _AmbientContext.reset_for_testing()
            import aictl
            t0 = time.perf_counter()
            r1 = aictl.ai.ask(prompt, model=first_model)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            first_response = str(r1)
            total_cost = r1.cost
        except Exception as e:
            warn(f"Simple model failed: {e}")
            return 1

        # Step 2: quality check
        if not _cascade_quality_ok(first_response, min_length):
            # Escalate to complex model
            escalated = True
            complex_model = cfg["complex"]["model"]
            model = complex_model
            try:
                _AmbientContext.reset_for_testing()
                t0 = time.perf_counter()
                r2 = aictl.ai.ask(prompt, model=complex_model)
                elapsed_ms += int((time.perf_counter() - t0) * 1000)
                response_text = str(r2)
                total_cost += r2.cost
            except Exception as e:
                warn(f"Complex model also failed: {e}")
                return 1
        else:
            model = first_model
            response_text = first_response

    if use_json:
        result: dict = {
            "prompt": prompt,
            "score": score,
            "tier": tier,
            "first_model": first_model,
            "final_model": model,
            "escalated": escalated,
            "response": response_text,
            "total_cost": total_cost,
            "latency_ms": elapsed_ms,
        }
        if first_response and escalated:
            result["first_response"] = first_response
        print_json(result)
    else:
        print()
        if escalated:
            ok(f"Cascade: {first_model} → escalated → {model} (score={score})")
        else:
            ok(f"Cascade: {model} (score={score}, no escalation needed)")
        print()
        print(response_text)
        print()
        label = "escalated" if escalated else "direct"
        print(f"  Cost: {total_cost}  Latency: {elapsed_ms}ms  Path: {label}")
        print()
    _record_cascade_stat(escalated)
    return 0


def run_stats(args: argparse.Namespace) -> int:
    """Show cascade routing statistics from persistent counter file."""
    stats: dict[str, int] = {"total_runs": 0, "escalations": 0}
    try:
        stats = json.loads(_cascade_stats_path().read_text())
    except Exception:
        pass  # file absent → all zeros

    total = int(stats.get("total_runs", 0))
    escalations = int(stats.get("escalations", 0))
    direct = total - escalations
    rate = escalations / total if total > 0 else 0.0

    use_json = getattr(args, "json", False)
    if use_json:
        print_json({
            "total_runs": total,
            "direct": direct,
            "escalations": escalations,
            "escalation_rate": round(rate, 4),
        })
        return 0

    print()
    if total == 0:
        warn("No cascade runs recorded yet.")
        print("  Try: aictl route cascade 'What is 2+2?'")
        print()
        return 0
    print("  ── Cascade Routing Statistics ─────────────────────────")
    print()
    print(f"  Total runs:       {total:>6}")
    print(f"  Direct (no esc):  {direct:>6}  ({1.0 - rate:.1%})")
    print(f"  Escalated:        {escalations:>6}  ({rate:.1%})")
    print()
    ok("Stats from ~/.aios/cascade_stats.json")
    print()
    return 0


def _cascade_quality_ok(response: str, min_words: int) -> bool:
    """Heuristic quality gate for cascade routing.

    Returns False (triggering escalation) when the response is too short
    OR contains uncertainty phrases that indicate the small model gave up.
    """
    words = response.split()
    if len(words) < min_words:
        return False
    lower = response.lower()
    uncertainty_phrases = [
        "i don't know", "i'm not sure", "i cannot", "i can't",
        "as an ai", "i don't have", "beyond my", "not able to",
        "i apologize", "unfortunately i",
    ]
    return not any(phrase in lower for phrase in uncertainty_phrases)


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


def _cascade_stats_path() -> Path:
    base = os.environ.get("AIOS_STATE_DIR", os.path.expanduser("~/.aios"))
    return Path(base) / "cascade_stats.json"


def _record_cascade_stat(escalated: bool) -> None:
    """Increment persistent cascade run/escalation counters (best-effort)."""
    try:
        path = _cascade_stats_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        stats: dict[str, int] = {"total_runs": 0, "escalations": 0}
        if path.exists():
            try:
                stats = json.loads(path.read_text())
            except Exception:
                pass
        stats["total_runs"] = int(stats.get("total_runs", 0)) + 1
        if escalated:
            stats["escalations"] = int(stats.get("escalations", 0)) + 1
        path.write_text(json.dumps(stats))
    except Exception:
        pass  # stats are advisory; never crash the main path


def _load_config() -> dict[str, Any]:
    """Load data from persistent storage."""
    path = _config_path()
    if path.exists():
        try:
            cfg = json.loads(path.read_text())
            # Backfill not just a missing tier but a tier dict missing required
            # sub-keys. `setdefault(tier, defaults)` alone left a hand-edited
            # `{"medium": {"max_score": 60}}` without a "model", so the later
            # `cfg[tier]["model"]` raised KeyError — surfaced as a bogus
            # "Invalid input: model" for a perfectly valid prompt.
            for tier, defaults in _DEFAULT_TIERS.items():
                if not isinstance(cfg.get(tier), dict):
                    cfg[tier] = dict(defaults)
                else:
                    for k, v in defaults.items():
                        cfg[tier].setdefault(k, v)
            return cfg
        except Exception:
            pass  # best-effort; failure is non-critical
    return dict(_DEFAULT_TIERS)


def _save_config(cfg: dict[str, Any]) -> None:
    """Persist data to storage."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: a crash mid-save must not corrupt the tier config.
    atomic_write_text(path, json.dumps(cfg, indent=2, ensure_ascii=False))


# ── Embedding-kNN tie-breaker (IMPROVEMENTS.md item C-1) ───

# Tiers the kNN verdict is allowed to move the regex tier to. A 2-tier jump
# (SIMPLE<->COMPLEX) is never trusted from a handful of nearest neighbors —
# if the regex scorer and the embedding bank disagree that badly, something
# is wrong with one of them, not a case for the tie-breaker to resolve.
_ADJACENT_TIERS = {
    "SIMPLE": {"MEDIUM"},
    "MEDIUM": {"SIMPLE", "COMPLEX"},
    "COMPLEX": {"MEDIUM"},
}

_KNN_CACHE_LOCK = threading.Lock()
_KNN_BANK_MEMO: dict[str, Any] | None = None  # in-process memo; avoids re-reading disk every call

# Re-attempt a semantic build if the last cached attempt degraded to the hash
# fallback and is older than this — an embedding model may become reachable
# later in a long-lived process (e.g. an engine started after aictl did).
_KNN_CACHE_RETRY_AFTER_S = 3600


def _knn_cache_path() -> Path:
    """Return the path to the kNN example-bank embedding cache file."""
    base = os.environ.get("AIOS_STATE_DIR", os.path.expanduser("~/.aios"))
    return Path(base) / "route_knn_cache.json"


def _reset_knn_cache_for_testing() -> None:
    """Clear the in-process kNN bank memo (test isolation only; disk cache untouched)."""
    global _KNN_BANK_MEMO
    with _KNN_CACHE_LOCK:
        _KNN_BANK_MEMO = None


def _knn_examples_hash() -> str:
    """Stable hash of the labeled example set; auto-invalidates the disk
    cache if _KNN_EXAMPLES is ever edited."""
    payload = json.dumps(_KNN_EXAMPLES, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def _knn_bank_is_fresh(entry: dict[str, Any], bank_hash: str) -> bool:
    if entry.get("bank_hash") != bank_hash:
        return False
    if entry.get("semantic"):
        return True
    return (time.time() - float(entry.get("built_at", 0))) < _KNN_CACHE_RETRY_AFTER_S


def _get_knn_bank() -> tuple[list[dict[str, Any]], bool]:
    """Return (bank, semantic) for the labeled kNN example set.

    `bank` is a list of {"tier", "prompt", "vector"} dicts, one per
    _KNN_EXAMPLES entry. `semantic` is True iff those vectors are real
    embeddings rather than the SHA-256 hash fallback. Disk+memory cached,
    keyed on a hash of _KNN_EXAMPLES so edits to the labeled set
    auto-invalidate the cache; self-heals by retrying the embed after
    _KNN_CACHE_RETRY_AFTER_S if the last attempt degraded to the fallback.
    """
    global _KNN_BANK_MEMO

    bank_hash = _knn_examples_hash()

    with _KNN_CACHE_LOCK:
        if _KNN_BANK_MEMO is not None and _knn_bank_is_fresh(_KNN_BANK_MEMO, bank_hash):
            return _KNN_BANK_MEMO["bank"], _KNN_BANK_MEMO["semantic"]

    cache_path = _knn_cache_path()
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            if isinstance(cached, dict) and _knn_bank_is_fresh(cached, bank_hash):
                with _KNN_CACHE_LOCK:
                    _KNN_BANK_MEMO = cached
                return cached["bank"], cached["semantic"]
        except Exception:
            pass  # corrupt/unreadable cache — rebuild below

    from aictl.core.rag import embed_text, FALLBACK_DIM
    prompts = [p for _, p in _KNN_EXAMPLES]
    try:
        vectors = embed_text(prompts)
    except Exception:
        vectors = []

    semantic = (
        len(vectors) == len(prompts)
        and all(len(v) != FALLBACK_DIM for v in vectors)
    )
    if len(vectors) != len(prompts):
        vectors = [[] for _ in prompts]

    bank = [
        {"tier": tier, "prompt": prompt, "vector": vector}
        for (tier, prompt), vector in zip(_KNN_EXAMPLES, vectors)
    ]
    entry = {"bank_hash": bank_hash, "semantic": semantic, "built_at": time.time(), "bank": bank}

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(cache_path, json.dumps(entry))
    except Exception:
        pass  # cache write is best-effort; kNN still usable from memory this process

    with _KNN_CACHE_LOCK:
        _KNN_BANK_MEMO = entry
    return bank, semantic


def route_tier_gated(prompt: str, cfg: Any = None, force: bool = False) -> tuple[str, dict[str, Any]]:
    """Return (tier, meta): the regex-scored tier, optionally refined by a
    confidence-gated embedding-kNN tie-breaker.

    The regex scorer (score_complexity/classify_complexity) always runs
    first and is the authoritative verdict. kNN is consulted only as a
    tie-breaker, and only when every one of these holds:
      - route_knn_enabled is set in global config, or `force` is True
      - the regex score falls within route_knn_margin of the 30/60 tier
        boundary (this prompt is a genuine toss-up, not a clear call)
      - the labeled example bank has real (non-fallback) embeddings
      - the live prompt's own embedding is also real (non-fallback)
      - the top-k neighbor vote reaches route_knn_min_agreement
      - the kNN verdict is an ADJACENT tier only (never a 2-tier jump)
    Any exception at any step silently abstains to the regex verdict — this
    is a tie-breaker, never a replacement, and must never turn a working
    router into a broken one.
    """
    score = score_complexity(prompt)
    regex_tier = classify_complexity(score)
    meta: dict[str, Any] = {
        "score": score,
        "regex_tier": regex_tier,
        "knn_applied": False,
        "knn_tier": None,
        "knn_agreement": None,
    }

    # Aliased import: route.py's own _load_config()/_config_path() are the
    # LOCAL tier-model config (route_config.json); this is the GLOBAL
    # aictl.core.config.Config that holds the route_knn_* fields. Same name
    # ("load_config") in the upstream module, so it must be aliased here to
    # avoid shadowing this file's pre-existing local _load_config.
    from aictl.core.config import load_config as load_global_config
    gcfg = cfg if cfg is not None else load_global_config()

    if not (force or gcfg.route_knn_enabled):
        return regex_tier, meta

    margin = gcfg.route_knn_margin
    near_boundary = abs(score - 30) <= margin or abs(score - 60) <= margin
    if not near_boundary:
        return regex_tier, meta

    try:
        bank, semantic_bank = _get_knn_bank()
        if not semantic_bank or not bank:
            return regex_tier, meta

        from aictl.core.rag import embed_text, cosine, FALLBACK_DIM
        [query_vec] = embed_text([prompt])
        if not query_vec or len(query_vec) == FALLBACK_DIM:
            return regex_tier, meta

        k = max(1, gcfg.route_knn_k)
        neighbors = heapq.nlargest(
            k, bank, key=lambda ex: cosine(query_vec, ex["vector"]),
        )
        if not neighbors:
            return regex_tier, meta

        votes: dict[str, int] = {}
        for ex in neighbors:
            votes[ex["tier"]] = votes.get(ex["tier"], 0) + 1
        winner, winner_count = max(votes.items(), key=lambda kv: kv[1])
        agreement = winner_count / len(neighbors)

        meta["knn_tier"] = winner
        meta["knn_agreement"] = round(agreement, 3)

        if agreement < gcfg.route_knn_min_agreement:
            return regex_tier, meta
        if winner == regex_tier:
            return regex_tier, meta
        if winner not in _ADJACENT_TIERS.get(regex_tier, set()):
            return regex_tier, meta

        meta["knn_applied"] = True
        return winner, meta
    except Exception:
        return regex_tier, meta

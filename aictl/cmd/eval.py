"""aictl eval — LLM regression testing framework.

Most teams in 2026 are at Level 0-1 eval maturity (Confident AI survey).
No existing CLI tool has built-in LLM evaluation. This fills that gap.

What it solves:
  - Prompt changes that silently break quality (no exception thrown)
  - Model swaps (llama → qwen) with unknown behavioral impact
  - CI/CD integration: catch regressions before deployment

Usage:
  aictl eval create  --suite ./evals/summarize.json   # template
  aictl eval run     --suite ./evals/summarize.json   # run all cases
  aictl eval compare --suite ./evals/summarize.json --baseline ./baseline.json
  aictl eval report  --suite ./evals/summarize.json   # human-readable

Assertion types (stdlib only, zero deps):
  contains        value in output
  not_contains    value NOT in output
  max_length      len(output) ≤ N chars
  min_length      len(output) ≥ N chars
  json_valid      output is valid JSON
  regex           re.search(pattern, output)
  starts_with     output starts with value
  latency_ms      response time ≤ N ms
  cost_usd        per-call cost ≤ N USD
  llm_judge       use another local model to score 1-5, pass if ≥ threshold
"""

from __future__ import annotations

import argparse

from typing import Any

import json
import re
import time
from pathlib import Path


# ── Default eval suite template ───────────────────────────

_TEMPLATE = {
    "name": "my-eval-suite",
    "model": "auto",
    "description": "Regression tests for prompt quality",
    "cases": [
        {
            "id": "basic-factual",
            "prompt": "What is the capital of Japan?",
            "assertions": [
                {"type": "contains", "value": "Tokyo"},
                {"type": "max_length", "value": 500},
                {"type": "latency_ms", "value": 5000},
            ],
        },
        {
            "id": "json-output",
            "prompt": "Return a JSON object with keys: name, year. Name = aictl, year = 2026.",
            "assertions": [
                {"type": "json_valid"},
                {"type": "contains", "value": "aictl"},
            ],
        },
        {
            "id": "safety-check",
            "prompt": "Tell me how to make my Python code faster.",
            "assertions": [
                {"type": "not_contains", "value": "sudo rm -rf"},
                {"type": "min_length", "value": 30},
            ],
        },
    ],
}


# ── Assertion engine ─────────────────────────────────────

def _check_assertion(assertion: dict[str, Any], output: str, latency_ms: int, cost_usd: float) -> tuple[bool, str]:
    """Run one assertion. Returns (passed, reason)."""
    kind = assertion.get("type", "")
    value = assertion.get("value")

    if kind == "contains":
        passed = str(value) in output
        return passed, f"output {'contains' if passed else 'MISSING'} {value!r}"

    if kind == "not_contains":
        passed = str(value) not in output
        return passed, f"output {'does not contain' if passed else 'CONTAINS (BAD)'} {value!r}"

    if kind == "max_length":
        if value is None:
            return False, "max_length assertion missing 'value'"
        passed = len(output) <= int(value)
        return passed, f"length {len(output)} {'≤' if passed else '>'} {value}"

    if kind == "min_length":
        if value is None:
            return False, "min_length assertion missing 'value'"
        passed = len(output) >= int(value)
        return passed, f"length {len(output)} {'≥' if passed else '<'} {value}"

    if kind == "json_valid":
        try:
            json.loads(output)
            return True, "valid JSON"
        except json.JSONDecodeError as e:
            return False, f"invalid JSON: {e}"

    if kind == "regex":
        passed = bool(re.search(str(value), output, re.IGNORECASE))
        return passed, f"regex {value!r} {'matched' if passed else 'NOT MATCHED'}"

    if kind == "starts_with":
        stripped = output.strip()
        passed = stripped.startswith(str(value))
        return passed, f"output {'starts with' if passed else 'does NOT start with'} {value!r}"

    if kind == "latency_ms":
        if value is None:
            return False, "latency_ms assertion missing 'value'"
        passed = latency_ms <= int(value)
        return passed, f"latency {latency_ms}ms {'≤' if passed else '>'} {value}ms"

    if kind == "cost_usd":
        if value is None:
            return False, "cost_usd assertion missing 'value'"
        passed = cost_usd <= float(value)
        return passed, f"cost ${cost_usd:.6f} {'≤' if passed else '>'} ${value}"

    if kind == "llm_judge":
        return _llm_judge(output, assertion)

    return False, f"unknown assertion type: {kind!r}"


def _llm_judge(output: str, assertion: dict[str, Any]) -> tuple[bool, str]:
    """Use a local model to score output quality (1-5)."""
    threshold = int(assertion.get("threshold", 3))
    rubric = assertion.get("rubric", "Is this a helpful, accurate, and relevant response?")
    try:
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()
        import aictl
        judge_prompt = (
            f"Rate this AI output on a scale of 1-5.\n\n"
            f"Rubric: {rubric}\n\n"
            f"Output to rate:\n{output[:500]}\n\n"
            f"Reply with ONLY a number 1-5."
        )
        verdict = aictl.ai.ask(judge_prompt, mode="factual")
        score_match = re.search(r"[1-5]", str(verdict))
        if score_match:
            score = int(score_match.group())
            passed = score >= threshold
            return passed, f"LLM judge score {score}/5 (threshold {threshold})"
        return False, "LLM judge returned unparseable score"
    except Exception as e:
        return False, f"LLM judge error: {e}"


# ── Runner ───────────────────────────────────────────────

def _run_case(case: dict[str, Any], model: str) -> dict[str, Any]:
    """Run one eval case and return a result dict."""
    case_id = case.get("id", "unnamed")
    prompt_template = case.get("prompt", "")
    inputs = case.get("inputs", {})
    assertions = case.get("assertions", [])

    # Substitute {{variable}} placeholders
    prompt = prompt_template
    for k, v in inputs.items():
        prompt = prompt.replace(f"{{{{{k}}}}}", str(v))

    # Inference
    t0 = time.perf_counter()
    try:
        import aictl
        response = aictl.ai.ask(prompt)
        output = str(response)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        cost_usd = response.cost_usd
        error = None
    except Exception as e:
        output = ""
        latency_ms = int((time.perf_counter() - t0) * 1000)
        cost_usd = 0.0
        error = str(e)

    # Evaluate assertions
    results = []
    for assertion in assertions:
        passed, reason = _check_assertion(assertion, output, latency_ms, cost_usd)
        results.append({
            "type": assertion.get("type"),
            "passed": passed,
            "reason": reason,
        })

    all_passed = error is None and all(r["passed"] for r in results)

    return {
        "id": case_id,
        "passed": all_passed,
        "error": error,
        "output_preview": output[:100] if output else "",
        "latency_ms": latency_ms,
        "cost_usd": cost_usd,
        "assertions": results,
    }


# ── CLI handlers ─────────────────────────────────────────

def register(sub: Any) -> None:
    """Register CLI subcommand."""
    p = sub.add_parser(
        "eval",
        help="LLM regression testing — catch prompt quality regressions.",
    )
    p.add_argument("--json", action="store_true", help="Output as JSON")
    sp = p.add_subparsers(dest="eval_cmd", required=False)

    c = sp.add_parser("create", help="Create a new eval suite template.")
    c.add_argument("--suite", default="./eval_suite.json", help="Output path")
    c.set_defaults(func=run_create)

    r = sp.add_parser("run", help="Run an eval suite against the current model.")
    r.add_argument("--suite", required=True, help="Eval suite JSON file")
    r.add_argument("--model", default="auto", help="Model to use (override suite)")
    r.add_argument("--save", help="Save results to file for comparison")
    r.add_argument("--json", action="store_true", help="JSON output")
    r.set_defaults(func=run_eval)

    cmp = sp.add_parser("compare", help="Compare two eval results (before/after).")
    cmp.add_argument("--suite", required=True, help="Eval suite JSON")
    cmp.add_argument("--baseline", required=True, help="Baseline results JSON")
    cmp.add_argument("--json", action="store_true", help="JSON output")
    cmp.set_defaults(func=run_compare)

    rpt = sp.add_parser("report", help="Human-readable eval summary.")
    rpt.add_argument("--suite", required=True, help="Eval suite or results JSON")
    rpt.set_defaults(func=run_report)

    p.set_defaults(func=run_default)


def run_default(args: argparse.Namespace) -> int:
    """Default: show help."""
    print()
    print("  aictl eval — LLM regression testing")
    print()
    print("  Commands:")
    print("    aictl eval create  --suite ./evals.json      # create template")
    print("    aictl eval run     --suite ./evals.json      # run tests")
    print("    aictl eval compare --suite ./evals.json --baseline ./v1.json")
    print("    aictl eval report  --suite ./evals.json      # summary")
    print()
    print("  Example workflow:")
    print("    aictl eval run --suite evals.json --save baseline.json")
    print("    # ... change model or prompt ...")
    print("    aictl eval run --suite evals.json --save new.json")
    print("    aictl eval compare --suite evals.json --baseline baseline.json")
    print()
    return 0


def run_create(args: argparse.Namespace) -> int:
    """Create an eval suite template."""
    suite_path = Path(getattr(args, "suite", "./eval_suite.json"))
    if suite_path.exists():
        from aictl.core.output import warn
        warn(f"{suite_path} already exists. Delete it first to recreate.")
        return 1
    suite_path.parent.mkdir(parents=True, exist_ok=True)
    suite_path.write_text(json.dumps(_TEMPLATE, indent=2, ensure_ascii=False))
    from aictl.core.output import ok
    ok(f"Created {suite_path}")
    print()
    print("  Edit the suite, then run:")
    print(f"    aictl eval run --suite {suite_path}")
    print()
    return 0


def run_eval(args: argparse.Namespace) -> int:
    """Run an eval suite."""
    suite_path = Path(args.suite)
    if not suite_path.exists():
        from aictl.core.output import err
        err(f"Suite not found: {suite_path}")
        print(f"  Create one: aictl eval create --suite {suite_path}")
        return 1

    suite = json.loads(suite_path.read_text())
    cases = suite.get("cases", [])
    model = getattr(args, "model", "auto") or suite.get("model", "auto")

    if not cases:
        from aictl.core.output import warn
        warn("No test cases in suite.")
        return 0

    use_json = getattr(args, "json", False)
    if not use_json:
        print()
        print(f"  Running {len(cases)} eval cases  [{suite.get('name', suite_path.stem)}]")
        print()

    results = []
    passed = 0

    for case in cases:
        case_id = case.get("id", "unnamed")
        result = _run_case(case, model)
        results.append(result)

        if result["passed"]:
            passed += 1

        if not use_json:
            icon = "✓" if result["passed"] else "✗"
            latency = f"{result['latency_ms']}ms"
            cost = f"${result['cost_usd']:.6f}" if result["cost_usd"] > 0 else "$0 (cached)"
            print(f"  {icon}  {case_id:<32} {latency:>7}  {cost}")
            if not result["passed"]:
                for ar in result["assertions"]:
                    if not ar["passed"]:
                        print(f"       ↳ FAIL [{ar['type']}] {ar['reason']}")

    total = len(cases)
    failed = total - passed

    # Save results
    output = {
        "suite": suite.get("name", suite_path.stem),
        "model": model,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": passed / total if total > 0 else 0,
        "cases": results,
    }

    save_path = getattr(args, "save", None)
    if save_path:
        Path(save_path).write_text(json.dumps(output, indent=2))
        if not use_json:
            print()
            print(f"  Results saved: {save_path}")

    if use_json:
        from aictl.core.output import print_json
        print_json(output)
        return 0 if failed == 0 else 1

    print()
    if failed == 0:
        from aictl.core.output import ok
        ok(f"All {total} cases passed  (pass rate 100%)")
    else:
        from aictl.core.output import warn, err
        err(f"{failed}/{total} cases FAILED  (pass rate {passed*100//total}%)")

    if getattr(args, "save", None):
        pass  # already printed
    else:
        from aictl.core.next_action import suggest
        suggest("eval_run")

    print()
    return 0 if failed == 0 else 1


def run_compare(args: argparse.Namespace) -> int:
    """Compare two eval result files."""
    suite_path = Path(args.suite)
    baseline_path = Path(args.baseline)

    if not baseline_path.exists():
        from aictl.core.output import err
        err(f"Baseline not found: {baseline_path}")
        return 1

    # Run current eval first
    run_args = argparse.Namespace(
        suite=str(suite_path), model="auto", save=None, json=False)

    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        run_eval(run_args)
    buf.getvalue()

    # Re-run properly to get data
    suite = json.loads(suite_path.read_text())
    current = {
        "suite": suite.get("name", "current"),
        "cases": [_run_case(c, "auto") for c in suite.get("cases", [])],
    }

    baseline = json.loads(baseline_path.read_text())

    # Build comparison
    base_by_id = {c["id"]: c for c in baseline.get("cases", [])}
    curr_by_id = {c["id"]: c for c in current.get("cases", [])}

    use_json = getattr(args, "json", False)

    regressions = []
    improvements = []
    stable = []

    for case_id, curr in curr_by_id.items():
        base = base_by_id.get(case_id)
        if base is None:
            continue
        if base["passed"] and not curr["passed"]:
            regressions.append(case_id)
        elif not base["passed"] and curr["passed"]:
            improvements.append(case_id)
        else:
            stable.append(case_id)

    if use_json:
        from aictl.core.output import print_json
        print_json({
            "regressions": regressions,
            "improvements": improvements,
            "stable": stable,
            "baseline_pass_rate": baseline.get("pass_rate", 0),
        })
        return 1 if regressions else 0

    print()
    print(f"  Eval comparison  [{baseline.get('suite', '?')}]")
    print(f"  Baseline: {baseline_path}")
    print()

    if regressions:
        print(f"  ✗ REGRESSIONS ({len(regressions)} cases broke):")
        for r in regressions:
            print(f"    ↳ {r}  was PASS → now FAIL")
        print()

    if improvements:
        print(f"  ✓ IMPROVEMENTS ({len(improvements)} cases fixed):")
        for r in improvements:
            print(f"    ↳ {r}  was FAIL → now PASS")
        print()

    if not regressions and not improvements:
        from aictl.core.output import ok
        ok("No regressions or improvements detected. Behavior stable.")
    elif regressions:
        from aictl.core.output import err
        err(f"{len(regressions)} regression(s) detected — review before deploying.")

    print()
    return 1 if regressions else 0


def run_report(args: argparse.Namespace) -> int:
    """Print a human-readable eval report."""
    suite_path = Path(args.suite)
    if not suite_path.exists():
        from aictl.core.output import err
        err(f"Not found: {suite_path}")
        return 1

    data = json.loads(suite_path.read_text())

    # Accept either a suite definition or saved results
    if "cases" in data and "passed" in data.get("cases", [{}])[0]:
        # It's a results file
        results = data
    else:
        # It's a suite definition — run it
        run_args = argparse.Namespace(
            suite=str(suite_path), model="auto", save=None, json=False)
        run_eval(run_args)
        return 0

    print()
    print(f"  Eval Report: {results.get('suite', suite_path.stem)}")
    print(f"  Model:       {results.get('model', 'unknown')}")
    print(f"  Run at:      {results.get('timestamp', 'unknown')}")
    print()

    total = results.get("total", 0)
    passed = results.get("passed", 0)
    failed = total - passed
    pass_rate = passed / total * 100 if total > 0 else 0

    print(f"  {'PASS':>6}  {'FAIL':>6}  {'RATE':>6}")
    print(f"  {passed:>6}  {failed:>6}  {pass_rate:>5.0f}%")
    print()

    for case in results.get("cases", []):
        icon = "✓" if case["passed"] else "✗"
        print(f"  {icon} {case['id']}")
        if not case["passed"]:
            for ar in case.get("assertions", []):
                if not ar.get("passed", True):
                    print(f"    [{ar['type']}] {ar['reason']}")

    print()
    return 0

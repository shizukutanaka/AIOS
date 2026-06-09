"""aictl setup — guided first-run experience.

Apple's Setup Assistant philosophy applied to CLI:
  - One question at a time
  - Sensible defaults for every question
  - Progress is visible
  - At the end, something actually works
"""

from __future__ import annotations

from typing import Any

import argparse

import io
import sys
import time
from contextlib import redirect_stdout

from aictl.core.output import ok, warn, err
from aictl.core.state import StateStore


def register(sub: Any) -> None:
    """Register CLI subcommand."""
    p = sub.add_parser(
        "setup",
        help="Guided first-run setup. Takes about 5 minutes.",
    )
    p.add_argument(
        "--non-interactive",
        action="store_true",
        help="Accept all defaults without prompting (CI/scripting).",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the guided setup."""
    interactive = not getattr(args, "non_interactive", False)
    store = StateStore(getattr(args, "state_dir", None))

    _banner()

    # Step 1: Hardware
    _step(1, "Checking your hardware")
    from aictl.runtime.broker import full_detect
    hw = full_detect()
    vram_mb = sum(g.vram_mb for g in hw.gpus)

    if hw.gpus:
        gpu_label = f"{len(hw.gpus)}× {hw.gpus[0].name}" if len(hw.gpus) > 1 else hw.gpus[0].name
        ok(f"{gpu_label} ({vram_mb // 1024}GB VRAM)  •  {hw.system.ram_total_mb // 1024}GB RAM")
    else:
        ok(f"CPU only  •  {hw.system.ram_total_mb // 1024}GB RAM")
        warn("No GPU found — inference will be slow on CPU.")
    print()

    # Step 2: Choose model
    _step(2, "Choosing a model for your hardware")
    from aictl.runtime.recommend import recommend
    candidates = recommend(vram_mb=vram_mb, ram_mb=hw.system.ram_total_mb, max_results=4)

    if not candidates:
        err("No models fit your hardware. Try: aictl configure --engine cloud")
        return 1

    print()
    print("  Models that fit:")
    print()
    for i, m in enumerate(candidates[:3], 1):
        vram_str = f"{m.vram_required_mb // 1024}GB" if m.vram_required_mb > 0 else "CPU"
        print(f"  [{i}] {m.name:<24} {vram_str:<6}  {m.use_case}")
    print()

    chosen = candidates[0]
    if interactive and sys.stdin.isatty():
        try:
            raw = input(f"  Your choice [1–{min(3, len(candidates))}] (Enter = {chosen.name}): ").strip()
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(candidates):
                    chosen = candidates[idx]
        except (EOFError, KeyboardInterrupt):
            pass  # best-effort; failure is non-critical

    ok(f"Selected: {chosen.name}")
    print()

    # Step 3: Engine check
    _step(3, "Checking inference engine")
    engine_ok = _check_engine(chosen.runtime)
    if not engine_ok:
        _engine_guide(chosen.runtime)
        if interactive and sys.stdin.isatty():
            try:
                input("  Press Enter when done, or Ctrl-C to skip: ")
            except (EOFError, KeyboardInterrupt):
                print()
                warn("Skipping engine install.")
                _completion(hw, chosen, False)
                return 0
        engine_ok = _check_engine(chosen.runtime)

    if engine_ok:
        ok(f"{chosen.runtime} is ready")
    else:
        warn(f"{chosen.runtime} still not found. Continuing without inference.")
    print()

    # Step 4: Pull model
    _step(4, f"Downloading {chosen.name}")
    pull_ok = _pull_model(chosen.name, chosen.runtime)
    if pull_ok:
        ok(f"{chosen.name} is ready")
    else:
        warn("Could not download automatically.")
        print(f"  Run: {chosen.runtime} pull {chosen.name}")
    print()

    # Step 5: First inference
    _step(5, "Testing with a real question")
    _first_inference(chosen.name, chosen.runtime)
    print()

    # Save config
    from aictl.core.config import load_config, save_config
    from aictl.cmd.init import run as init_run

    if not store.is_initialized():
        init_args = argparse.Namespace(
            force=False, json=False, state_dir=getattr(args, "state_dir", None))
        with redirect_stdout(io.StringIO()):
            init_run(init_args)

    config = load_config(store.dir)
    save_config(config, store.dir)

    _completion(hw, chosen, engine_ok)
    return 0


def _banner() -> None:
    """Print the Setup Assistant banner."""
    print()
    print("  ┌─────────────────────────────────────────────────────┐")
    print("  │                                                     │")
    print("  │   aictl Setup                     5 steps · ~5min  │")
    print("  │                                                     │")
    print("  └─────────────────────────────────────────────────────┘")
    print()


def _step(n: int, label: str) -> None:
    """Print a numbered step header."""
    print(f"  ── Step {n}/5: {label}")


def _check_engine(runtime: str) -> bool:
    """Validate or inspect the given state."""
    import subprocess
    try:
        if runtime == "ollama":
            return subprocess.run(["ollama", "list"], capture_output=True, timeout=5).returncode == 0
        if runtime == "vllm":
            import urllib.request
            urllib.request.urlopen("http://localhost:8000/health", timeout=3)
            return True
        return False
    except Exception:
        return False


def _engine_guide(runtime: str) -> None:
    """Print engine installation guidance."""
    print()
    warn(f"{runtime} is not running.")
    print()
    if runtime == "ollama":
        print("  Install Ollama:")
        print("    curl -fsSL https://ollama.com/install.sh | sh")
        print("    ollama serve &")
    elif runtime == "vllm":
        print("  Install vLLM:")
        print("    pip install vllm")
    print()


def _pull_model(name: str, runtime: str) -> bool:
    """Download a model from the engine registry."""
    import subprocess
    if runtime == "ollama":
        try:
            print(f"  Downloading {name}... (may take several minutes)")
            return subprocess.run(["ollama", "pull", name], timeout=600).returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
    return False


def _first_inference(name: str, runtime: str) -> None:
    """Run a sample inference to verify the setup works."""
    import subprocess
    question = "In one sentence, what is AI?"
    print(f"  Question: {question}")
    print()

    if runtime == "ollama":
        try:
            t0 = time.perf_counter()
            r = subprocess.run(
                ["ollama", "run", name, question],
                capture_output=True, text=True, timeout=60,
            )
            elapsed = time.perf_counter() - t0
            if r.returncode == 0 and r.stdout.strip():
                answer = r.stdout.strip()[:200]
                print(f"  Answer: {answer}")
                print()
                ok(f"It works! ({elapsed:.1f}s)")
                return
        except Exception:
            pass  # best-effort; failure is non-critical

    # Fallback: mock engine
    try:
        from aictl.sdk import _AmbientContext
        _AmbientContext.reset_for_testing()
        import aictl
        r = aictl.ai.ask(question)
        print(f"  Answer: {str(r)[:200]}")
        print()
        ok("SDK working (mock engine — connect Ollama for real inference)")
    except Exception as e:
        warn(f"Could not test inference: {e}")


def _completion(hw: Any, chosen: Any, engine_ready: bool) -> None:
    """Print the Setup Assistant completion screen."""
    print()
    print("  ╔═════════════════════════════════════════════════════╗")
    print("  ║   Setup complete.                                   ║")
    print("  ╚═════════════════════════════════════════════════════╝")
    print()

    if engine_ready:
        print(f"  Your AI ({chosen.name}) is ready:")
        print()
        print(f"    aictl chat '{chosen.name}'       # interactive chat")
        print("    aictl rag index ./docs            # index your documents")
        print("    aictl rag ask 'your question'     # query them")
        print()
        print("  From Python:")
        print("    import aictl")
        print("    aictl.ai.ask('Summarize this', context=text)")
    else:
        print("  When your engine is ready:")
        print()
        print("    aictl doctor       # verify everything works")
        print("    aictl recommend    # see model options again")
    print()
    print("  Explore: aictl help")
    print()

"""Example: classify customer support messages.

Run:  python3 examples/sdk/01_classify.py

This shows the simplest possible aictl SDK usage. No setup, no
configuration, no model choice — aictl picks what fits your hardware.
"""

import sys
from pathlib import Path

# Run straight from a clone, exactly as the README's install describes
# (`git clone && cd aios`) — no `pip install` step. Without this, running
# `python3 examples/sdk/<name>.py` puts examples/sdk/ on sys.path instead of
# the repository root, and `import aictl` fails with ModuleNotFoundError.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import aictl

messages = [
    "I love this product! It's been life-changing.",
    "I've been on hold for an hour. This is unacceptable.",
    "How do I reset my password?",
    "The shipping was fast, thank you!",
    "Why is my last payment showing as failed?",
]

print("Classifying customer messages...\n")

for msg in messages:
    result = aictl.ai.classify(
        msg,
        categories=["positive", "complaint", "question"],
    )
    # `result` is a str subclass, so `print(f"[{result}]")` above would have
    # worked and shown "positive" for every message here — that silent
    # failure is exactly what motivated `.matched`/`.mock` (see
    # aictl.sdk.Classification). Checking them is what actually shows this
    # ran against the in-process mock rather than a real model, and that
    # none of these five answers were a confident category match.
    flag = ""
    if result.mock:
        flag = "  (mock engine — not a real classification)"
    elif not result.matched:
        flag = "  (no category matched; showing the default)"
    print(f"  [{result:>9}]  {msg}{flag}")

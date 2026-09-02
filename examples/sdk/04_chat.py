"""Example: streaming chat completion.

Run:  python3 examples/sdk/04_chat.py
"""

import sys
from pathlib import Path

# Run straight from a clone, exactly as the README's install describes
# (`git clone && cd aios`) — no `pip install` step. Without this, running
# `python3 examples/sdk/<name>.py` puts examples/sdk/ on sys.path instead of
# the repository root, and `import aictl` fails with ModuleNotFoundError.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import aictl

print("Streaming a story word by word...\n")

response = aictl.ai.ask(
    "Write a 50-word story about a robot learning to garden.",
    stream=True,
)

# When stream=True the response is iterable
if hasattr(response, "__iter__") and not isinstance(response, str):
    for chunk in response:
        print(chunk, end="", flush=True)
    print()
else:
    # Fallback for non-streaming engines
    print(response)

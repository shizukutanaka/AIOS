"""Example: cost-aware operation with budget protection.

Run:  python3 examples/sdk/05_cost.py

aictl tracks token usage automatically and refuses to spend more than
the configured budget. Useful when running in production where a runaway
loop could otherwise generate a surprise bill.
"""

import sys
from pathlib import Path

# Run straight from a clone, exactly as the README's install describes
# (`git clone && cd aios`) — no `pip install` step. Without this, running
# `python3 examples/sdk/<name>.py` puts examples/sdk/ on sys.path instead of
# the repository root, and `import aictl` fails with ModuleNotFoundError.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import aictl

# Set a hard monthly cap (in USD-equivalent)
aictl.ai.configure(cost_budget_usd=5.00)

print("Status:")
status = aictl.ai.status          # a property, not a call
for key, value in status.items():
    print(f"  {key}: {value}")

# Run a small task
result = aictl.ai.ask(
    "In one sentence, what's the difference between cost and price?"
)
print(f"\nAnswer: {result}")

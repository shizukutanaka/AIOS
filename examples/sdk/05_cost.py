"""Example: cost-aware operation with budget protection.

Run:  python3 examples/sdk/05_cost.py

aictl tracks token usage automatically and refuses to spend more than
the configured budget. Useful when running in production where a runaway
loop could otherwise generate a surprise bill.
"""

import aictl

# Set a hard monthly cap (in USD-equivalent)
aictl.ai.configure(cost_budget_usd=5.00)

print("Status:")
status = aictl.ai.status()
for key, value in status.items():
    print(f"  {key}: {value}")

# Run a small task
result = aictl.ai.ask(
    "In one sentence, what's the difference between cost and price?"
)
print(f"\nAnswer: {result}")

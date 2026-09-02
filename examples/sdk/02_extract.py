"""Example: extract structured data from unstructured text.

Run:  python3 examples/sdk/02_extract.py

Needs a real model. Structured output requires the engine to emit JSON, and
the in-process mock returns prose — the other examples in this directory work
with no engine running, this one does not.
"""

import sys
from pathlib import Path

# Run straight from a clone, exactly as the README's install describes
# (`git clone && cd aios`) — no `pip install` step. Without this, running
# `python3 examples/sdk/<name>.py` puts examples/sdk/ on sys.path instead of
# the repository root, and `import aictl` fails with ModuleNotFoundError.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import aictl

invoice = """
Invoice #INV-2026-0421
Date: 2026-04-25
Bill to: Acme Corporation
Amount due: $4,250.00
Due date: 2026-05-15
Items: 2x Widget Pro ($1,500), 1x Premium Service ($1,250)
"""

result = aictl.ai.structured(
    "Extract the invoice details",
    schema={
        "type": "object",
        "properties": {
            "invoice_number": {"type": "string"},
            "amount_usd": {"type": "number"},
            "due_date": {"type": "string"},
            "customer": {"type": "string"},
        },
        "required": ["invoice_number", "amount_usd"],
    },
    context=invoice,
)

print("Extracted:")
for key, value in result.items():
    print(f"  {key:>16}: {value}")

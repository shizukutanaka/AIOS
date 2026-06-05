"""Example: extract structured data from unstructured text.

Run:  python3 examples/sdk/02_extract.py
"""

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

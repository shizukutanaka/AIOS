"""Example: classify customer support messages.

Run:  python3 examples/sdk/01_classify.py

This shows the simplest possible aictl SDK usage. No setup, no
configuration, no model choice — aictl picks what fits your hardware.
"""

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
    category = aictl.ai.classify(
        msg,
        categories=["positive", "complaint", "question"],
    )
    print(f"  [{category:>9}]  {msg}")

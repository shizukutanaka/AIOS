"""Example: streaming chat completion.

Run:  python3 examples/sdk/04_chat.py
"""

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

# aictl SDK

AI that just works.

```python
from aictl import ai

answer = ai.ask("What is the capital of France?")
print(answer)
```

No configuration. No API keys. No model selection. No installation of separate services.

## Three methods, that's the whole contract

```python
ai.ask("Summarize this email: ...")            # one-shot
ai.chat([{"role": "user", "content": "Hi"}])    # multi-turn
ai.embed("vector this text")                     # embeddings
```

## Semantic modes, not temperature values

```python
ai.ask("Why is the sky blue?",    mode="reasoning")
ai.ask("Write a poem about rain", mode="creative")
ai.ask("What year did Meiji end?",mode="factual")
ai.ask("Is this spam?",           mode="concise")
```

## Privacy as a one-line flag

```python
ai.ask("Confidential document content...", private=True)
```

`private=True` guarantees the request never leaves the device — even if local inference fails.

## Streaming

```python
for chunk in ai.chat(messages, stream=True):
    print(chunk, end="", flush=True)
```

## How it works

On first use, the SDK quietly:

1. Detects your hardware (GPU, RAM, CPU)
2. Picks the best model that fits
3. Finds a running engine (Ollama, vLLM, aictl proxy)
4. If nothing is running, spins up a mock for development

You configure nothing. The system adapts to the machine.

## Progressive disclosure

When you need control, it's there:

```python
ai.ask("...", model="llama3.1:70b")      # explicit model
ai.status                                  # diagnostic snapshot
```

When you need to go deeper, the full `aictl` CLI and API are available:

```bash
aictl doctor          # inspect
aictl deploy optimize # tune
aictl fleet status    # manage
```

But most code, most of the time, only needs the three methods above.

## Installation

```bash
pip install aictl
```

That's it. No separate daemon, no config files, no background services required.

The SDK works out of the box with any running Ollama / vLLM instance, and falls back to an in-process mock engine for development.

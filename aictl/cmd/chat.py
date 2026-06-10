"""aictl chat — interactive chat with inference engine."""

from __future__ import annotations

from typing import Any

import argparse
from aictl.core.constants import MOCK_ENGINE_PORT

import json
import urllib.request
from aictl.core.output import ok, err
from aictl.core.config import load_config
from aictl.core.state import StateStore


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("chat", help="Interactive chat with inference engine")
    p.add_argument("--model", default="", help="Model name")
    p.add_argument("--endpoint", default="", help="Override endpoint")
    p.add_argument("--system", default="You are a helpful assistant.", help="System prompt")
    p.add_argument("--mock", action="store_true", help="Use mock engine on mock port")
    p.add_argument("--stream", action="store_true", help="Stream tokens as they arrive")
    p.set_defaults(func=run)


def _stream_response(req: "urllib.request.Request") -> str:
    """Stream SSE tokens from an OpenAI-compatible endpoint; return full content."""
    import sys
    collected: list[str] = []
    print("\033[1;32mAI:\033[0m ", end="", flush=True)
    with urllib.request.urlopen(req, timeout=120) as r:
        for raw in r:
            line = raw.decode("utf-8", errors="replace").rstrip("\n\r")
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
                delta = chunk["choices"][0].get("delta", {})
                token = delta.get("content") or ""
                if token:
                    print(token, end="", flush=True)
                    collected.append(token)
            except Exception:
                pass
    print(f"\n  \033[2m({len(collected)} chunks)\033[0m\n")
    return "".join(collected)


def run(args: argparse.Namespace) -> int:
    """Execute the chat command."""
    endpoint = getattr(args, "endpoint", "")
    model = getattr(args, "model", "")

    if getattr(args, "mock", False):
        endpoint = f"http://127.0.0.1:{MOCK_ENGINE_PORT}"

    if not endpoint:
        store = StateStore(getattr(args, "state_dir", None))
        config = load_config(store.dir)
        endpoints = config.engines.to_dict()
        for name, ep in endpoints.items():
            endpoint = ep
            break

    if not endpoint:
        err("No endpoint. Use --endpoint or --mock, or start an engine.")
        return 1

    # Auto-detect model
    if not model:
        try:
            with urllib.request.urlopen(f"{endpoint.rstrip('/')}/v1/models", timeout=5) as r:
                data = json.loads(r.read())
                models = data.get("data", [])
                if models:
                    model = models[0].get("id", "default")
        except Exception:
            model = "default"

    ok(f"Chat with {model} at {endpoint}")
    print("  Type 'quit' to exit, 'clear' to reset history\n")

    system = getattr(args, "system", "You are a helpful assistant.")
    history: list[dict[str, Any]] = [{"role": "system", "content": system}]

    while True:
        try:
            prompt = input("\033[1;36mYou:\033[0m ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not prompt.strip():
            continue
        if prompt.strip().lower() == "quit":
            break
        if prompt.strip().lower() == "clear":
            history = [{"role": "system", "content": system}]
            print("  (history cleared)\n")
            continue

        history.append({"role": "user", "content": prompt})

        use_stream = getattr(args, "stream", False)
        try:
            body = json.dumps({
                "model": model,
                "messages": history,
                "max_tokens": 500,
                "stream": use_stream,
            }).encode()
            req = urllib.request.Request(
                f"{endpoint.rstrip('/')}/v1/chat/completions",
                data=body, headers={"Content-Type": "application/json"},
            )
            if use_stream:
                content = _stream_response(req)
            else:
                with urllib.request.urlopen(req, timeout=60) as r:
                    resp = json.loads(r.read())
                content = resp["choices"][0]["message"]["content"]
                tokens = resp.get("usage", {}).get("completion_tokens", 0)
                print(f"\033[1;32mAI:\033[0m {content}")
                print(f"  \033[2m({tokens} tokens)\033[0m\n")
            history.append({"role": "assistant", "content": content})
        except Exception as e:
            print(f"  \033[31mError: {e}\033[0m\n")
            history.pop()  # Remove failed user message

    return 0

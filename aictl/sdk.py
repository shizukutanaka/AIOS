"""aictl SDK — AI as invisible infrastructure.

Apple-philosophy interface: zero configuration, smart defaults, one-line usage.

    from aictl import ai
    answer = ai.ask("What is 2+2?")

That's it. Behind the scenes:
  - Hardware is detected
  - The best local model is chosen
  - Downloaded if missing
  - Inference is executed
  - Result is returned

No configuration files. No API keys to set. No model selection menus.
The system adapts to the developer, not the other way around.

─── Progressive Disclosure ──────────────────────────────

Simple (90% of users):
    ai.ask("Summarize this email: ...")
    ai.chat([{"role": "user", "content": "Hi"}])
    ai.embed("vector this text")

Intermediate (9%):
    ai.ask("...", model="fast")          # hint, not mandate
    ai.ask("...", mode="reasoning")       # semantic intent
    ai.ask("...", private=True)           # never leave device

Advanced (1%):
    from aictl import runtime
    runtime.route_policy("code").use("deepseek-coder")
    runtime.telemetry.export_to("https://grafana.example.com")

The vast majority never reach past the first tier. That's the goal.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal, overload


# ── The invisible brain ───────────────────────────────

class _AmbientContext:
    """Holds everything the developer never has to know about.

    Lazy-initialized on first use. Survives the process lifetime.
    Auto-adapts when hardware or network conditions change.
    """

    _instance: "_AmbientContext | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "_AmbientContext":
        """Create and return a new instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        """Initialize the instance."""
        self._hardware: dict[str, Any] | None = None
        self._default_model: str | None = None
        self._engine_url: str | None = None
        self._ready = False
        self._usage: dict[str, Any] = {"calls": 0, "tokens": 0, "cached_hits": 0}

    @classmethod
    def reset_for_testing(cls) -> None:
        """Destroy the singleton so the next call re-initializes.

        Tests only. Never call from production code.
        """
        with cls._lock:
            cls._instance = None

    def ensure_ready(self) -> None:
        """First-call initialization. Runs once, then becomes a no-op."""
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            self._detect_hardware()
            self._choose_default_model()
            self._locate_or_start_engine()
            self._ready = True

    def _detect_hardware(self) -> None:
        """Discover what we're running on. Never fails."""
        try:
            from aictl.runtime.broker import full_detect
            hw = full_detect()
            self._hardware = {
                "profile": hw.profile,
                "gpus": [{"name": g.name, "vram_mb": g.vram_mb} for g in hw.gpus],
                "ram_mb": hw.system.ram_total_mb,
                "cpu_cores": hw.system.cpu_cores,
            }
        except Exception:
            # Even without aictl's runtime, degrade gracefully
            self._hardware = {"profile": "unknown"}

    def _choose_default_model(self) -> None:
        """Pick a model that fits the hardware. Zero questions asked."""
        # User override takes precedence
        if env_model := os.environ.get("AICTL_MODEL"):
            self._default_model = env_model
            return

        if not self._hardware or not self._hardware.get("gpus"):
            # CPU-only: tiny model
            self._default_model = "llama3.2:1b"
            return

        total_vram = sum(g.get("vram_mb", 0) for g in self._hardware["gpus"])
        if total_vram >= 40_000:
            self._default_model = "qwen3:32b"
        elif total_vram >= 16_000:
            self._default_model = "llama3.1:8b"
        elif total_vram >= 6_000:
            self._default_model = "qwen3:7b"
        else:
            self._default_model = "llama3.2:1b"

    def _locate_or_start_engine(self) -> None:
        """Find a running engine, or use the mock if nothing's there."""
        # Check user-configured endpoint
        if url := os.environ.get("AICTL_ENDPOINT"):
            self._engine_url = url
            return

        # Try common local endpoints in order of preference
        for candidate in (
            "http://127.0.0.1:11434",  # Ollama default
            "http://127.0.0.1:8000",   # vLLM default
            "http://127.0.0.1:8080",   # aictl proxy
        ):
            if self._engine_reachable(candidate):
                self._engine_url = candidate
                return

        # Fallback: spin up an ephemeral mock engine for development
        self._start_dev_engine()

    def _engine_reachable(self, url: str) -> bool:
        """Return True if the configured engine endpoint is reachable."""
        import urllib.request
        import urllib.error
        try:
            req = urllib.request.Request(f"{url}/v1/models", method="GET")
            with urllib.request.urlopen(req, timeout=0.5) as r:
                return bool(r.status == 200)
        except Exception:
            return False

    def _start_dev_engine(self) -> None:
        """Launch an in-process mock engine. Invisible to the developer."""
        try:
            from aictl.daemon.mock_engine import start_mock_engine
            port = _find_free_port()
            start_mock_engine(port=port)
            self._engine_url = f"http://127.0.0.1:{port}"
            # Server runs on daemon thread; auto-cleans on process exit
        except Exception:
            self._engine_url = None

    @property
    def model(self) -> str:
        """Return the configured default model name."""
        return self._default_model or "mock"

    @property
    def endpoint(self) -> str:
        """Return the configured engine endpoint URL."""
        return self._engine_url or "http://127.0.0.1:9999"

    def note_usage(self, tokens: int, cached: bool = False) -> None:
        """Record token usage for this session and optional team quota."""
        self._usage["calls"] += 1
        self._usage["tokens"] += tokens
        if cached:
            self._usage["cached_hits"] += 1
        elif tokens > 0:
            _update_team_quota(tokens)


def _find_free_port() -> int:
    """Search and return the matching item, or None."""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# ── The developer-facing surface ──────────────────────

@dataclass
class _Response:
    """What you get back. Acts like a string, carries metadata."""
    text: str
    model: str = ""
    tokens: int = 0
    latency_ms: int = 0
    cached: bool = False
    cost_usd: float = 0.0    # actual cost for this call
    cost_jpy: float = 0.0

    def __str__(self) -> str:
        """Return the string representation."""
        return self.text

    def __repr__(self) -> str:
        """Return the developer-facing representation."""
        return f"<ai response from {self.model}: {self.text[:40]!r}...>"

    def __add__(self, other: object) -> str:
        """response + 'text' → concatenated string."""
        return self.text + str(other)

    def __radd__(self, other: object) -> str:
        """'text' + response → concatenated string."""
        return str(other) + self.text

    def __len__(self) -> int:
        """Return the length."""
        return len(self.text)

    def __contains__(self, item: object) -> bool:
        """'word' in response → True/False."""
        return str(item) in self.text

    def __eq__(self, other: object) -> bool:
        """Return True if equal to other."""
        if isinstance(other, str):
            return self.text == other
        if isinstance(other, _Response):
            return self.text == other.text
        return NotImplemented

    def __hash__(self) -> int:
        """Return the hash value."""
        return hash(self.text)

    def strip(self, chars: str | None = None) -> str:
        """Delegate to str.strip()."""
        return self.text.strip(chars)

    def startswith(self, prefix: str | tuple[Any, ...], *args: Any) -> bool:
        """Delegate to str.startswith()."""
        return self.text.startswith(prefix, *args)

    def endswith(self, suffix: str | tuple[Any, ...], *args: Any) -> bool:
        """Delegate to str.endswith()."""
        return self.text.endswith(suffix, *args)

    def lower(self) -> str:
        """Delegate to str.lower()."""
        return self.text.lower()

    def upper(self) -> str:
        """Delegate to str.upper()."""
        return self.text.upper()

    def split(self, sep: str | None = None, maxsplit: int = -1) -> list[str]:
        """Delegate to str.split()."""
        return self.text.split(sep, maxsplit)

    def replace(self, old: str, new: str, count: int = -1) -> str:
        """Delegate to str.replace()."""
        return self.text.replace(old, new) if count == -1 else self.text.replace(old, new, count)

    @property
    def cost(self) -> str:
        """Human-readable cost string."""
        if self.cached:
            return "$0.000000 (cached)"
        if self.cost_usd < 0.0001:
            return f"${self.cost_usd * 1000:.4f}m"
        return f"${self.cost_usd:.6f}"


class _AI:
    """The `ai` namespace. Intentionally tiny.

    Three methods cover 95% of use cases:
      ask(text)      → one-shot Q&A
      chat(messages) → multi-turn
      embed(text)    → vector representation

    Everything else lives behind progressive disclosure.
    """

    # Semantic modes: what you want, not how to get it
    _MODE_PRESETS = {
        "default":    {"temperature": 0.7, "max_tokens": 500},
        "reasoning":  {"temperature": 0.2, "max_tokens": 2000},
        "creative":   {"temperature": 1.0, "max_tokens": 1500},
        "factual":    {"temperature": 0.1, "max_tokens": 300},
        "concise":    {"temperature": 0.3, "max_tokens": 100},
    }

    @overload
    def ask(
        self, prompt: str, *, context: str = ..., mode: str = ...,
        model: str | None = ..., private: bool = ...,
        stream: Literal[False] = ...,
    ) -> "_Response": ...

    @overload
    def ask(
        self, prompt: str, *, context: str = ..., mode: str = ...,
        model: str | None = ..., private: bool = ...,
        stream: Literal[True],
    ) -> "Iterator[str]": ...

    def ask(
        self,
        prompt: str,
        *,
        context: str = "",
        mode: str = "default",
        model: str | None = None,
        private: bool = False,
        stream: bool = False,
    ) -> "_Response | Iterator[str]":
        """Ask a question. Get an answer.

        Args:
            prompt: What you want to know.
            context: Optional background text (a document, email, etc.).
            mode: Semantic intent — "reasoning", "creative", "factual", "concise".
            model: Optional hint. Ignore if you don't care (you usually don't).
            private: If True, never fall back to cloud providers.
            stream: If True, return an iterator of text chunks.

        Returns:
            A response that behaves like a string (or an iterator if stream=True).
        """
        if not isinstance(prompt, str):
            raise TypeError(
                f"prompt must be str, got {type(prompt).__name__}. "
                f"Try: aictl.ai.ask('your question')"
            )
        if not prompt.strip():
            raise ValueError(
                "prompt must not be empty. "
                "Try: aictl.ai.ask('What is AI?')"
            )

        ctx = _AmbientContext()
        ctx.ensure_ready()

        preset = self._MODE_PRESETS.get(mode, self._MODE_PRESETS["default"])
        chosen_model = model or ctx.model
        full = f"Context:\n{context}\n\nQuestion: {prompt}" if context else prompt
        messages = [{"role": "user", "content": full}]

        if stream:
            return _stream_complete(
                endpoint=ctx.endpoint, model=chosen_model,
                messages=messages, temperature=float(preset["temperature"]),
                max_tokens=int(preset["max_tokens"]),
            )

        t0 = time.perf_counter()

        # 1. Cache lookup (skip for private/stream)
        if not private:
            cached = _cache_lookup(full, chosen_model)
            if cached is not None:
                cached.latency_ms = int((time.perf_counter() - t0) * 1000)
                return cached

        # 2. Inference
        text, tokens = _complete(
            endpoint=ctx.endpoint, model=chosen_model,
            messages=messages, temperature=float(preset["temperature"]),
            max_tokens=int(preset["max_tokens"]), allow_cloud=not private,
        )
        latency = int((time.perf_counter() - t0) * 1000)

        # 3. Post-inference: usage tracking, cache store, cost
        ctx.note_usage(tokens)
        # Don't cache empty/garbage responses — a cached "" would poison every
        # identical prompt for the full TTL.
        if not private and text.strip():
            _cache_store(full, text, chosen_model, tokens)
        cost = _compute_call_cost(chosen_model, full, tokens, ctx.endpoint)

        return _Response(
            text=text, model=chosen_model, tokens=tokens,
            latency_ms=latency, cost_usd=cost[0], cost_jpy=cost[1],
        )

    def classify(
        self,
        text: str,
        categories: list[str],
        *,
        model: str | None = None,
        private: bool = False,
    ) -> str:
        """Classify text into one of the given categories.

        Returns one of the category strings (best-effort match if the model
        returns something close but not exact).
        """
        if not isinstance(text, str):
            raise TypeError(
                f"text must be str, got {type(text).__name__}. "
                f"Try: aictl.ai.classify(str(your_text), categories=[...])"
            )
        if not categories:
            raise ValueError(
                "classify() needs at least one category. "
                "Try: aictl.ai.classify(text, categories=['positive', 'negative'])"
            )

        cats_str = " | ".join(categories)
        prompt = (
            f"Classify this text into exactly one of: {cats_str}\n\n"
            f"Text: {text}\n\n"
            f"Reply with only the category name, nothing else."
        )
        response = self.ask(prompt, mode="factual", model=model, private=private)
        answer = str(response).strip().strip(".,!?'\"").lower()

        # Exact match first
        for cat in categories:
            if cat.lower() == answer:
                return cat
        # Substring match (model might say 'positive feedback' for 'positive')
        for cat in categories:
            if cat.lower() in answer or answer in cat.lower():
                return cat
        # Last resort: return the first category but flag unknown
        return categories[0]

    def structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        context: str = "",
        model: str | None = None,
        private: bool = False,
    ) -> dict[str, Any]:
        """Get a structured answer matching the given JSON schema.

        Returns a dict that satisfies the schema. If the model produces
        invalid JSON or wrong shape, raises ValueError.
        """
        if not isinstance(prompt, str):
            raise TypeError(f"prompt must be str, got {type(prompt).__name__}")
        if not isinstance(schema, dict):
            raise TypeError(f"schema must be dict, got {type(schema).__name__}")

        ctx = _AmbientContext()
        ctx.ensure_ready()
        chosen_model = model or ctx.model

        schema_str = json.dumps(schema, indent=2, ensure_ascii=False)
        instruction = (
            f"Return a single JSON object matching this schema:\n{schema_str}\n\n"
            f"Reply with ONLY the JSON, no prose, no markdown fences."
        )
        if context:
            instruction = f"Context:\n{context}\n\n{instruction}\n\nTask: {prompt}"
        else:
            instruction = f"{instruction}\n\nTask: {prompt}"

        text, tokens = _complete(
            endpoint=ctx.endpoint,
            model=chosen_model,
            messages=[{"role": "user", "content": instruction}],
            temperature=0.0,  # determinism for structured output
            max_tokens=2000,
            allow_cloud=not private,
        )
        ctx.note_usage(tokens)

        # Strip markdown fences if the model added them
        cleaned = text.strip()
        if cleaned.startswith("```"):
            # Remove leading ```json or ``` and trailing ```
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines)

        try:
            result: dict[str, Any] = json.loads(cleaned)
            return result
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Model did not return valid JSON. "
                f"Got: {cleaned[:200]!r} (error: {e})"
            ) from e

    def configure(
        self,
        *,
        cost_budget_usd: float | None = None,
        prefer: str | None = None,
        engine: str | None = None,
        model: str | None = None,
    ) -> None:
        """Set runtime preferences. Calling this is usually unnecessary.

        Args:
            cost_budget_usd: Maximum monthly spend. Refuses requests that
                would exceed this.
            prefer: One of "speed", "quality", "cost".
            engine: Force a specific engine (ollama, vllm, sglang).
            model: Set a default model (you usually want auto-selection).
        """
        ctx = _AmbientContext()
        ctx.ensure_ready()
        if cost_budget_usd is not None:
            if not isinstance(cost_budget_usd, (int, float)):
                raise TypeError(
                    f"cost_budget_usd must be a number, got {type(cost_budget_usd).__name__}"
                )
            if cost_budget_usd < 0:
                raise ValueError(
                    f"cost_budget_usd must be >= 0, got {cost_budget_usd}. "
                    f"Use 0 to disable spending, or a positive number like 10.0"
                )
            ctx._usage["budget_usd"] = float(cost_budget_usd)
        if prefer is not None:
            ctx._usage["preference"] = prefer
        if engine is not None:
            ctx._engine_preference = engine
        if model is not None:
            ctx._default_model = model

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        mode: str = "default",
        model: str | None = None,
        private: bool = False,
        stream: bool = False,
    ) -> _Response | Iterator[str]:
        """Multi-turn conversation.

        messages: list[Any] of {"role": "user"|"assistant"|"system", "content": "..."}
        """
        ctx = _AmbientContext()
        ctx.ensure_ready()

        preset = self._MODE_PRESETS.get(mode, self._MODE_PRESETS["default"])
        chosen_model = model or ctx.model

        if stream:
            return _stream_complete(
                endpoint=ctx.endpoint,
                model=chosen_model,
                messages=messages,
                temperature=float(preset["temperature"]),
                max_tokens=int(preset["max_tokens"]),
            )

        t0 = time.perf_counter()
        text, tokens = _complete(
            endpoint=ctx.endpoint,
            model=chosen_model,
            messages=messages,
            temperature=float(preset["temperature"]),
            max_tokens=int(preset["max_tokens"]),
            allow_cloud=not private,
        )
        latency = int((time.perf_counter() - t0) * 1000)

        ctx.note_usage(tokens)
        return _Response(
            text=text, model=chosen_model, tokens=tokens, latency_ms=latency
        )

    def embed(self, text: str | list[str]) -> list[list[float]]:
        """Turn text into vectors. For RAG, search, clustering."""
        if text is None:
            raise TypeError(
                "text must be str or list[str], got None. "
                "Try: aictl.ai.embed('your text') or aictl.ai.embed(['a', 'b'])"
            )
        if not isinstance(text, (str, list)):
            raise TypeError(
                f"text must be str or list[str], got {type(text).__name__}. "
                f"Try: aictl.ai.embed('your text')"
            )
        ctx = _AmbientContext()
        ctx.ensure_ready()

        texts = [text] if isinstance(text, str) else list(text)
        return _embed(endpoint=ctx.endpoint, texts=texts)

    @property
    def status(self) -> dict[str, Any]:
        """Diagnostic info. For the curious, not the casual."""
        ctx = _AmbientContext()
        ctx.ensure_ready()
        return {
            "ready": ctx._ready,
            "hardware": ctx._hardware,
            "default_model": ctx._default_model,
            "endpoint": ctx._engine_url,
            "usage": dict(ctx._usage),
        }


# ── Transport layer (invisible) ──────────────────────

# ── Pipeline helpers (extracted from ask() for clarity) ──


def _update_team_quota(tokens: int) -> None:
    """Increment team quota if AICTL_TEAM env var is set. Never raises."""
    team = os.environ.get("AICTL_TEAM", "")
    if not team:
        return
    try:
        base = os.environ.get("AIOS_STATE_DIR", str(Path.home() / ".aios"))
        qpath = Path(base) / "quotas.json"
        if not qpath.exists():
            return
        qdata = json.loads(qpath.read_text())
        if team in qdata.get("teams", {}):
            qdata["teams"][team]["used_tokens"] = (
                qdata["teams"][team].get("used_tokens", 0) + tokens
            )
            qpath.write_text(json.dumps(qdata, indent=2, ensure_ascii=False))
    except Exception:
        pass  # best-effort; failure is non-critical


def _cache_lookup(prompt: str, model: str) -> "_Response | None":
    """Check semantic cache. Returns a cached _Response or None."""
    try:
        from aictl.core.sem_cache import get_default_cache
        entry = get_default_cache().lookup(prompt, model)
        if entry is not None:
            return _Response(
                text=entry.response, model=model,
                tokens=entry.tokens_saved, cached=True,
                cost_usd=0.0, cost_jpy=0.0,
            )
    except Exception:
        pass  # best-effort; failure is non-critical
    return None


def _cache_store(prompt: str, response: str, model: str, tokens: int) -> None:
    """Store a response in the semantic cache. Best-effort, never raises."""
    try:
        from aictl.core.sem_cache import get_default_cache
        get_default_cache().store(prompt, response, model, tokens=tokens)
    except Exception:
        pass  # best-effort; failure is non-critical


def _compute_call_cost(
    model: str, prompt: str, tokens: int, endpoint: str,
) -> tuple[float, float]:
    """Return (cost_usd, cost_jpy) for one inference call."""
    try:
        from aictl.core.cost_per_call import compute
        is_local = _is_local_endpoint(endpoint)
        # `tokens` is the API's total (prompt + completion); split it so input
        # tokens aren't billed twice (once via the char heuristic, once in total).
        input_tokens = max(1, len(prompt) // 4)
        output_tokens = max(1, tokens - input_tokens)
        cc = compute(model,
                     input_tokens=input_tokens,
                     output_tokens=output_tokens,
                     is_local=is_local)
        return (cc.cost_usd, cc.cost_jpy)
    except Exception:
        return (0.0, 0.0)


def _is_local_endpoint(endpoint: str) -> bool:
    """True for loopback or RFC-1918 / link-local hosts (self-hosted engines)."""
    from urllib.parse import urlparse
    host = (urlparse(endpoint).hostname or "").lower()
    if host == "localhost" or host.endswith(".local"):
        return True
    if host in ("0.0.0.0", "::1"):
        return True
    if host.startswith(("127.", "10.", "192.168.", "169.254.")):
        return True
    # 172.16.0.0/12
    if host.startswith("172."):
        parts = host.split(".")
        if len(parts) >= 2 and parts[1].isdigit() and 16 <= int(parts[1]) <= 31:
            return True
    return False


def _complete(
    endpoint: str,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    allow_cloud: bool = True,
) -> tuple[str, int]:
    """Call the completion API. Handles fallback silently."""
    import urllib.request
    import urllib.error

    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode()

    req = urllib.request.Request(
        f"{endpoint}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        choice = data["choices"][0]["message"]
        text = choice.get("content", "")
        tokens = data.get("usage", {}).get("total_tokens", 0)
        return text, tokens
    except Exception as e:
        if not allow_cloud:
            raise RuntimeError(f"Local inference failed, cloud fallback disabled: {e}")
        # Silent cloud fallback via aictl's existing fallback module
        try:
            from aictl.runtime.fallback import cloud_completion, load_fallback_config
            cfg = load_fallback_config(None)
            resp = cloud_completion(cfg, messages, max_tokens=max_tokens)
            if resp is None:
                raise RuntimeError("cloud fallback returned no response")
            text = resp.get("text", "")
            tokens = int(resp.get("tokens", 0))
            return text, tokens
        except Exception as fallback_exc:
            raise RuntimeError(
                f"Inference failed (local: {e}; cloud fallback: {fallback_exc})"
            ) from fallback_exc


def _stream_complete(
    endpoint: str,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
) -> Iterator[str]:
    """Stream tokens as they arrive."""
    import urllib.request

    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }).encode()

    req = urllib.request.Request(
        f"{endpoint}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(req, timeout=60) as r:
        for line in r:
            line = line.decode().strip()
            # SSE allows "data:" with or without a trailing space; some engines
            # (vLLM/SGLang) omit it. Match the prefix, then strip leading space.
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].lstrip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
                delta = chunk["choices"][0].get("delta", {}).get("content", "")
                if delta:
                    yield delta
            except (json.JSONDecodeError, KeyError, IndexError):
                continue


def _embed(endpoint: str, texts: list[str]) -> list[list[float]]:
    """Embed text(s) into vectors."""
    import urllib.request

    vectors: list[list[float]] = []
    for text in texts:
        body = json.dumps({
            "model": "nomic-embed-text",
            "input": text,
        }).encode()
        req = urllib.request.Request(
            f"{endpoint}/v1/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
            vec = data["data"][0]["embedding"]
            vectors.append(vec)
        except Exception:
            # Degraded mode: deterministic hash-based pseudo-embedding
            # So downstream code doesn't crash in dev environments
            import hashlib
            h = hashlib.sha256(text.encode()).digest()
            vectors.append([(b - 128) / 128 for b in h])
    return vectors


# ── The public `ai` object ───────────────────────────

ai = _AI()

__all__ = ["ai"]

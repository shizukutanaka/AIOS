# ADR-003: Hard filter + soft score routing algorithm

## Status: Accepted

## Context
Multiple inference engines (vLLM, SGLang, Ollama) may be running. Need intelligent routing.

## Decision
Two-phase routing: hard filter (reachability, model loaded, health) then soft score (SLO fit, headroom, cache, cost, power) with objective-specific weights.

## Rationale
- Hard filter prevents routing to broken engines
- Soft score optimizes for user's stated objective (latency/throughput/cost/balanced)
- Weight presets avoid complex per-request configuration
- Fallback chain (vLLM → SGLang → Ollama) ensures availability

## Consequences
- Score calculation adds ~50ms per route decision
- Requires metrics scraping from each engine

---
name: add-recipe
description: Add a new built-in recipe to aictl
argument-hint: [recipe-name]
---
# Add Recipe to aictl

1. Add recipe definition to `aictl/stack/manifest.py` in the `RECIPES` dict:
```python
RECIPES["$ARGUMENTS"] = {
    "name": "$ARGUMENTS",
    "version": "1",
    "services": [
        {"name": "svc", "runtime": "vllm|ollama|sglang", "model": "...",
         "port": 8000, "health_path": "/health", "gpu_required": True},
    ],
    "models": [{"name": "...", "format": "gguf|safetensors"}],
    "trust_policy": "warn",
}
```

2. Verify: `python3 -m aictl recipe list`
3. Test: add test case in `test_phase*.py`
4. Update README.md recipe table

# Skill: Add a built-in recipe

## Steps

1. Add recipe dict to `RECIPES` in `aictl/stack/manifest.py`
2. Each service needs: name, runtime (ollama|vllm|sglang), model, port
3. Set `gpu_required: True` if GPU is needed
4. Add test in `tests/test_aictl.py` → `TestStackManifest`
5. Verify Quadlet generation: `generate_quadlets(get_recipe("<n>"))`

## Template

```python
"my-recipe": {
    "name": "my-recipe",
    "version": "1",
    "services": [
        {
            "name": "llm",
            "runtime": "ollama",
            "model": "llama3.2:3b",
            "port": 11434,
        },
    ],
    "trust_policy": "warn",
},
```

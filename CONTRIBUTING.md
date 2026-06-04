# Contributing to aictl

## Development Setup

```bash
git clone https://github.com/shizukutanaka/aios.git
cd aios
python3 -m aictl --version   # No pip install needed
python3 -m aictl demo --auto  # Verify everything works
```

## Running Tests

```bash
python3 -m unittest discover -s tests    # All tests
python3 -m aictl gate                     # Full quality gate
python3 -m aictl bench --mock -n 10       # Benchmark
```

## Architecture Rules

1. **Zero external Python dependencies** — stdlib only
2. **Every module must compile** — `python3 -m py_compile <file>`
3. **All constants in `aictl/core/constants.py`** — no hardcoded ports/versions
4. **Docstrings on all public functions**
5. **Tests for every module** — `tests/test_<module>.py`

## Design Philosophy

- **John Carmack**: Performance-first, practical implementation
- **Robert C. Martin**: Clean architecture, SOLID principles
- **Rob Pike**: Simplicity, concurrency, "constants in one place"

## Adding a New Command

1. Create `aictl/cmd/<name>.py` with `register(sub)` and `run(args)` functions
2. Import in `aictl/__main__.py`
3. Add `<name>.register(sub)` to `build_parser()`
4. Add tests in `tests/test_<name>.py`
5. Support `--json` flag for structured output

## Adding a New Model to the DB

Edit `aictl/runtime/recommend.py` — add a `ModelRec(...)` entry to `MODELS` list.

## Adding a New Recipe

Edit `aictl/stack/manifest.py` — add to `RECIPES` dict with `StackManifest(...)`.

## Code Review Checklist

- [ ] `python3 -m py_compile` passes
- [ ] Tests added and passing
- [ ] `--json` flag supported
- [ ] Docstrings present
- [ ] No external dependencies added
- [ ] Constants use `aictl/core/constants.py`

## License

MIT — all contributions are under the same license.

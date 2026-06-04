---
name: code-review
description: Review code changes against project standards
---
# Code Review

## Checklist
1. **No external deps**: All imports must be from Python stdlib
2. **Test coverage**: New code has matching tests
3. **Registration**: New commands registered in `__main__.py`
4. **Output format**: Commands support `--json` flag
5. **Naming**: Functions use snake_case, classes PascalCase
6. **Docstrings**: All public functions have docstrings
7. **Error handling**: No bare `except:`, always `except Exception`
8. **State**: State changes go through `StateStore` methods

## Run
```bash
python3 -m unittest discover -s tests  # Must pass
python3 -m aictl doctor                # Smoke test
```

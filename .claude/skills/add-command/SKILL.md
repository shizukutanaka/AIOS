---
name: add-command
description: Add a new CLI command to aictl
argument-hint: [command-name]
---
# Add Command to aictl

Steps to add a new command `$ARGUMENTS`:

1. Create `aictl/cmd/$ARGUMENTS.py` with:
   - `register(sub)` function that adds an argparse subparser
   - `run(args) -> int` function
   - Support `--json` flag via `getattr(args, "json", False)`

2. Register in `aictl/__main__.py`:
   - Add to import line
   - Call `$ARGUMENTS.register(sub)` in `build_parser()`

3. Add tests in `tests/test_phase*.py`:
   - Parser test: verify args parse correctly
   - Functional test: verify the command works

4. Run full test suite:
   ```bash
   python3 -m unittest discover -s tests
   ```

5. Verify:
   ```bash
   python3 -m aictl $ARGUMENTS --help
   ```

## Template
```python
"""aictl $ARGUMENTS — description."""
from __future__ import annotations
from aictl.core.output import ok, err, print_json

def register(sub):
    p = sub.add_parser("$ARGUMENTS", help="Description")
    p.set_defaults(func=run)

def run(args) -> int:
    if getattr(args, "json", False):
        print_json({"status": "ok"})
        return 0
    ok("Done")
    return 0
```

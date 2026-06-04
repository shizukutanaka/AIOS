# Skill: Add a new aictl command

## Steps

1. Create `aictl/cmd/<name>.py` with:
   - `register(sub)` function that adds argparse subparser
   - `run(args) -> int` function
   - Support `--json` output via `getattr(args, "json", False)`

2. Import in `aictl/__main__.py`:
   - Add to import line
   - Call `<name>.register(sub)` in `build_parser()`

3. Add tests in `tests/test_<name>.py`:
   - Parser test: verify args parse correctly
   - Logic test: verify command logic with mocked state

4. Run: `python3 -m unittest discover -s tests`

## Template

```python
"""aictl <name> — description."""

from aictl.core.output import ok, err, print_json
from aictl.core.state import StateStore


def register(sub):
    p = sub.add_parser("<name>", help="...")
    p.set_defaults(func=run)


def run(args) -> int:
    store = StateStore(getattr(args, "state_dir", None))
    if getattr(args, "json", False):
        print_json({})
        return 0
    ok("Done")
    return 0
```

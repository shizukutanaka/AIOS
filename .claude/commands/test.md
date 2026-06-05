Run the full test suite and report results.

```bash
cd $CLAUDE_PROJECT_DIR
python3 -m unittest discover -s tests -v 2>&1 | tail -20
```

If tests fail, identify the failing test and suggest a fix.

Report project statistics.

```bash
cd $CLAUDE_PROJECT_DIR
echo "Version: $(python3 -m aictl --version 2>&1)"
echo "Tests:   $(python3 -m unittest discover -s tests 2>&1 | grep '^Ran')"
echo "Modules: $(find aictl/ -name '*.py' ! -name '__init__.py' | wc -l)"
echo "Py LOC:  $(find aictl/ tests/ -name '*.py' | xargs wc -l | tail -1)"
echo "Go LOC:  $(find go-port/ -name '*.go' | xargs wc -l | tail -1)"
echo "Commands: $(python3 -m aictl --help 2>&1 | grep '{' | tr ',' '\n' | wc -l)"
```

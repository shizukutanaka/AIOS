Compile-check all Python modules.

```bash
cd $CLAUDE_PROJECT_DIR
find aictl/ -name '*.py' -exec python3 -m py_compile {} \; 2>&1
echo "All modules compiled OK"
```

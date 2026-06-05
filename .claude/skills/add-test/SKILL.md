---
name: add-test
description: Write tests following project conventions
argument-hint: [module-name]
---
# Add Tests

## Convention
- File: `tests/test_<module>.py`
- Import path hack: `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`
- Use `unittest.TestCase`, NOT pytest
- No external deps (no pytest, no mock library — use unittest.mock)
- Temp dirs: `tempfile.mkdtemp()` or `t.TempDir()` (Go)

## Template
```python
import sys, unittest, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aictl.module.name import FunctionToTest

class TestFunctionToTest(unittest.TestCase):
    def test_basic(self):
        result = FunctionToTest()
        self.assertIsNotNone(result)

if __name__ == "__main__":
    unittest.main()
```

## Daemon endpoint tests
- Start ThreadedHTTPServer on random high port (177xx)
- Use setUpClass/tearDownClass
- Verify JSON response structure

## Run
```bash
python3 -m unittest discover -s tests          # All
python3 -m unittest tests.test_<module>         # Single
python3 -m aictl selftest                       # From CLI
```

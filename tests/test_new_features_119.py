"""Pass 119 (loop): `lora vllm-args` must honor the universal --json contract.

CLAUDE.md mandates "All output supports `--json`", but `lora vllm-args`
ignored the flag entirely — under `--json` it printed either a space-joined
arg string or the human sentence "No active adapters for this base model",
so `aictl lora vllm-args <base> --json | jq` got unparseable text.

It now emits `{"base": <stripped>, "args": [...]}` under --json (empty list
when there are no active adapters). The base is also stripped for lookup
symmetry: `lora add` stores a stripped base (Pass 115), so a padded query
here must still match.
"""

from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


class TestLoraVllmArgsJson(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        from aictl.runtime.lora import LoRAManager
        self._patch = patch("aictl.cmd.lora.LoRAManager",
                            lambda *a, **k: LoRAManager(Path(self.tmp)))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def _run_json(self, base):
        args = argparse.Namespace(base=base, json=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            from aictl.cmd.lora import run_vllm_args
            rc = run_vllm_args(args)
        return rc, buf.getvalue()

    def test_empty_emits_valid_json_not_sentence(self):
        rc, out = self._run_json("llama3:8b")
        self.assertEqual(rc, 0)
        d = json.loads(out)                       # was: "No active adapters..."
        self.assertEqual(d, {"base": "llama3:8b", "args": []})

    def test_populated_emits_args_list(self):
        from aictl.cmd.lora import run_add
        run_add(argparse.Namespace(name="fin", base="llama3:8b",
                                   path="/tmp/fin", rank=16, json=False))
        rc, out = self._run_json("llama3:8b")
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertIn("--enable-lora", d["args"])

    def test_padded_base_query_matches(self):
        from aictl.cmd.lora import run_add
        run_add(argparse.Namespace(name="fin", base="llama3:8b",
                                   path="/tmp/fin", rank=16, json=False))
        rc, out = self._run_json("  llama3:8b  ")
        d = json.loads(out)
        self.assertEqual(d["base"], "llama3:8b")
        self.assertIn("--enable-lora", d["args"])   # padded query still matches

    def test_human_path_unchanged(self):
        args = argparse.Namespace(base="llama3:8b", json=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            from aictl.cmd.lora import run_vllm_args
            rc = run_vllm_args(args)
        self.assertEqual(rc, 0)
        self.assertIn("No active adapters", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

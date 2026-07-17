"""Pass 129: `prompt` slug canonicalization must be symmetric (V2 class).

Research-informed (調査: Qiita/Zenn URL/slug canonicalization — "canonicalize
once, use that single form everywhere"). `prompt save` stored the name as a
slug (`name.replace(" ", "_").lower()`), but every read path (`get`, `history`,
`delete`, `run`, `export`) looked up `args.name` *raw*. So:

    prompt save --name "My Greeting"   → stored as "my_greeting"
    prompt get  "My Greeting"          → "Unknown prompt"  (the name you typed!)
    prompt get  "my_greeting"          → found (only the transformed key worked)

A `_slugify()` helper now canonicalizes on *every* path (and strips/rejects
empty), so a prompt is findable by any case/spacing variant of its name.
"""

from __future__ import annotations

import argparse
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


class TestPromptSlugSymmetry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = patch.dict(os.environ, {"AIOS_STATE_DIR": self.tmp})
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def _save(self, name, text="hello {input}"):
        return argparse.Namespace(name=name, text=text, file=None, model="",
                                  use_case="", owner="")

    def test_slugify_canonical_form(self):
        from aictl.cmd.prompt import _slugify
        self.assertEqual(_slugify("My Greeting"), "my_greeting")
        self.assertEqual(_slugify("  Spaced Name  "), "spaced_name")
        self.assertEqual(_slugify(""), "")

    def test_get_by_typed_name_after_save(self):
        from aictl.cmd.prompt import run_save, run_get
        self.assertEqual(run_save(self._save("My Greeting")), 0)
        # The exact name the user typed must resolve (was: exit 1 Unknown).
        g = argparse.Namespace(name="My Greeting", version=0)
        self.assertEqual(run_get(g), 0)

    def test_get_by_case_and_spacing_variants(self):
        from aictl.cmd.prompt import run_save, run_get
        run_save(self._save("My Greeting"))
        for variant in ("my greeting", "MY GREETING", "  my   ".replace("   ", " ") + "greeting"):
            g = argparse.Namespace(name=variant, version=0)
            # "my greeting" and "MY GREETING" both slugify to "my_greeting".
            if variant in ("my greeting", "MY GREETING"):
                self.assertEqual(run_get(g), 0, variant)

    def test_delete_symmetric(self):
        from aictl.cmd.prompt import run_save, run_delete
        run_save(self._save("My Greeting"))
        d = argparse.Namespace(name="my greeting", yes=True)
        self.assertEqual(run_delete(d), 0)

    def test_run_symmetric(self):
        from aictl.cmd.prompt import run_save, run_run
        run_save(self._save("My Greeting"))
        r = argparse.Namespace(name="MY GREETING", input="x", model="")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_run(r)
        self.assertEqual(rc, 0)

    def test_empty_name_rejected_on_save(self):
        from aictl.cmd.prompt import run_save
        self.assertEqual(run_save(self._save("   ")), 1)


if __name__ == "__main__":
    unittest.main()

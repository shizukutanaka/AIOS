"""Pass 151: `aictl import` must warn on a newer bundle export_version.

長所短所改善点 audit of the export/import round-trip: `run` read
`bundle.get("export_version", "1")` but only echoed it back in the result — it
NEVER validated it. A bundle from a newer aictl (export_version "2", carrying
sections/fields this importer doesn't understand) was silently best-effort
imported as its v1-compatible subset, with no signal to the operator that data
may have been dropped.

改良: a forward-compatibility guard. The import still runs (a partial import is
useful), but when the bundle's MAJOR version exceeds SUPPORTED_EXPORT_VERSION the
result carries a clear warning to upgrade. An unparseable version degrades to v1
(no spurious warning), and a same/older version is unaffected.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path


def _bundle(version):
    d = Path(tempfile.mkdtemp())
    f = d / "bundle.json"
    f.write_text(json.dumps({"export_version": version, "stacks": [], "models": []}))
    return f, Path(tempfile.mkdtemp())


def _import_json(bundle_path, state_dir):
    from aictl.cmd import import_cmd
    args = argparse.Namespace(file=str(bundle_path), json=True, dry_run=True,
                              skip_models=False, skip_stacks=False,
                              state_dir=str(state_dir))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = import_cmd.run(args)
    return code, json.loads(buf.getvalue())


class TestMajorVersion(unittest.TestCase):
    def test_parsing(self):
        from aictl.cmd.import_cmd import _major_version
        self.assertEqual(_major_version("1"), 1)
        self.assertEqual(_major_version("2"), 2)
        self.assertEqual(_major_version("2.3"), 2)
        self.assertEqual(_major_version(3), 3)

    def test_unparseable_defaults_to_supported(self):
        from aictl.cmd.import_cmd import _major_version, SUPPORTED_EXPORT_VERSION
        self.assertEqual(_major_version("weird"), SUPPORTED_EXPORT_VERSION)
        self.assertEqual(_major_version(None), SUPPORTED_EXPORT_VERSION)


class TestImportVersionGuard(unittest.TestCase):
    def test_v1_no_warning(self):
        f, sd = _bundle("1")
        code, out = _import_json(f, sd)
        self.assertEqual(code, 0)
        self.assertEqual(out["warnings"], [])

    def test_newer_version_warns(self):
        f, sd = _bundle("2")
        code, out = _import_json(f, sd)
        self.assertEqual(code, 0)   # still best-effort imports
        self.assertTrue(any("newer than supported" in w for w in out["warnings"]))

    def test_unparseable_version_no_spurious_warning(self):
        f, sd = _bundle("weird")
        code, out = _import_json(f, sd)
        self.assertEqual(out["warnings"], [])

    def test_missing_version_treated_as_v1(self):
        d = Path(tempfile.mkdtemp())
        f = d / "b.json"
        f.write_text(json.dumps({"stacks": [], "models": []}))  # no export_version
        code, out = _import_json(f, Path(tempfile.mkdtemp()))
        self.assertEqual(out["warnings"], [])


if __name__ == "__main__":
    unittest.main()

"""Pass 230: `aictl apply` crashed on the Kubernetes manifests this repo ships.

Running `aictl apply --validate-only` over every shipped manifest — the tool
exists precisely for this and nothing had used it on them — produced a crash on
two files in `examples/k8s/`:

    Unexpected error: ComposerError: expected a single document in the stream

Every Kubernetes manifest is `---`-separated, and this repository ships such
files, so the tool crashed on its own examples and told the user to file a bug
about it.

**The root cause is one line narrower than it looks.** The loader wrapped
`yaml.safe_load` in `except ImportError` only, so *every* YAML parse error
escaped to the CLI's generic handler. JSON and TOML never had this problem for
a precise reason: `json.JSONDecodeError` and `tomllib.TOMLDecodeError` are both
`ValueError` subclasses, which the handler already treats as bad input, while
`yaml.YAMLError` derives from `Exception` and is not. Same three lines of code
in each branch, different exception ancestry, only one of them broken.

Verified rather than assumed: malformed JSON and TOML already produced clean
`Invalid input:` messages; only YAML produced `Unexpected error:`.

**A finding I nearly got wrong.** Most of the ten shipped manifests fail the
stack validator, and the first reading was "eight broken examples". They are
not: `tenant-class.regulated.yaml`, `model-bundle.attested.yaml` and the rest
are CRD-style resources for a *different* API, so a stack validator rejecting
them is correct behaviour. Counting failures is not measuring them.

What *was* wrong there: `docs/ai_os/examples/` holds design examples for
`aios/v1alpha1` — the control-plane API labelled NOT IMPLEMENTED last pass —
including one named `stack.local-rag.yaml`, identical in name to the real
working manifest in `examples/` and incompatible with it. The spec was
labelled; its examples were not.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REAL_MANIFEST = Path("examples/stack.local-rag.yaml")
DESIGN_EXAMPLES = sorted(Path("docs/ai_os/examples").glob("*.yaml"))


def _validate(path: str) -> subprocess.CompletedProcess:
    import os

    env = dict(os.environ)
    env["AIOS_STATE_DIR"] = tempfile.mkdtemp(prefix="aictl-mf-")
    return subprocess.run(
        [sys.executable, "-m", "aictl", "apply", "-f", str(path), "--validate-only"],
        capture_output=True, text=True, timeout=120,
        cwd=str(Path(__file__).resolve().parent.parent), env=env)


class TestBadInputIsNotACrash(unittest.TestCase):
    """A user's malformed file must never be reported as an aictl bug."""

    def _write(self, name: str, body: str) -> str:
        path = Path(tempfile.mkdtemp(prefix="aictl-bad-")) / name
        path.write_text(body)
        return str(path)

    def test_multi_document_yaml_is_explained(self):
        result = _validate(self._write("k8s.yaml", "a: 1\n---\nb: 2\n"))
        self.assertNotIn("Unexpected error", result.stdout + result.stderr)
        self.assertIn("multiple YAML documents", result.stdout + result.stderr)

    def test_the_message_says_what_to_do_instead(self):
        result = _validate(self._write("k8s.yaml", "a: 1\n---\nb: 2\n"))
        self.assertIn("kubectl", result.stdout + result.stderr)

    def test_malformed_yaml_is_a_clean_error(self):
        result = _validate(self._write("bad.yaml", "a:\n  - b\n :bad\n"))
        combined = result.stdout + result.stderr
        self.assertNotIn("Unexpected error", combined)
        self.assertIn("Invalid YAML", combined)

    def test_malformed_json_was_already_clean(self):
        # Pinned to record why only YAML broke: JSONDecodeError is a
        # ValueError, so the handler already caught it.
        result = _validate(self._write("bad.json", "{ not json"))
        self.assertNotIn("Unexpected error", result.stdout + result.stderr)

    def test_no_format_reports_a_user_error_as_a_bug(self):
        for name, body in (("bad.yaml", "a:\n  - b\n :bad\n"),
                           ("bad.json", "{ not json"),
                           ("k8s.yaml", "a: 1\n---\nb: 2\n")):
            result = _validate(self._write(name, body))
            combined = result.stdout + result.stderr
            self.assertNotIn("report at", combined, name)


class TestShippedManifestsValidate(unittest.TestCase):
    def test_the_real_example_validates(self):
        result = _validate(str(REAL_MANIFEST))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_every_shipped_kubernetes_manifest_is_handled_cleanly(self):
        # They are not stack manifests and should be rejected — but rejected,
        # not crashed on.
        for path in sorted(Path("examples/k8s").glob("*.yaml")):
            result = _validate(str(path))
            combined = result.stdout + result.stderr
            self.assertNotIn("Unexpected error", combined, str(path))


class TestDesignExamplesAreLabelled(unittest.TestCase):
    """They target an API labelled NOT IMPLEMENTED; they must say so."""

    def test_there_are_design_examples(self):
        self.assertTrue(DESIGN_EXAMPLES)

    def test_each_one_says_it_is_not_implemented(self):
        for path in DESIGN_EXAMPLES:
            self.assertIn("NOT IMPLEMENTED", path.read_text(), str(path))

    def test_each_one_points_at_the_working_format(self):
        for path in DESIGN_EXAMPLES:
            self.assertIn("examples/stack.local-rag.yaml", path.read_text(),
                          str(path))

    def test_the_duplicate_name_is_disambiguated(self):
        # Two files named stack.local-rag.yaml in one repository, in
        # incompatible formats. The design one now says which is which.
        design = Path("docs/ai_os/examples/stack.local-rag.yaml").read_text()
        self.assertIn("aios/v1alpha1", design)
        self.assertNotIn("NOT IMPLEMENTED", REAL_MANIFEST.read_text())

    def test_the_index_says_which_api_they_target(self):
        self.assertIn("未実装", Path("docs/ai_os/README.md").read_text())


if __name__ == "__main__":
    unittest.main()

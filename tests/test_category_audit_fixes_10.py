"""Pass 10 regression tests for correctness bugs identified by deep audit."""

import unittest


class TestAdaptersNoDeadMonotonic(unittest.TestCase):
    """adapters.py: _http_get must not have a dead time.monotonic() call."""

    def test_no_dead_monotonic_in_http_get(self):
        import pathlib, re
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "runtime" / "adapters.py").read_text()
        # Pattern: a standalone time.monotonic() call that is NOT assigned to a variable
        # i.e. the line is exactly '        time.monotonic()' (indented, no assignment)
        dead = re.search(r"^\s+time\.monotonic\(\)\s*$", src, re.MULTILINE)
        self.assertIsNone(
            dead,
            "adapters.py _http_get must not contain a standalone (dead) time.monotonic() call; "
            "the result was computed but never stored, doing nothing useful",
        )

    def test_http_get_returns_tuple(self):
        from aictl.runtime.adapters import _http_get
        # Should return (int, str) even for non-existent URLs
        code, body = _http_get("http://localhost:1/nonexistent", timeout=1)
        self.assertIsInstance(code, int)
        self.assertIsInstance(body, str)


class TestDisaggArgAnnotation(unittest.TestCase):
    """disagg.py: _deployment() args parameter must be annotated as list[str], not argparse.Namespace."""

    def test_no_argparse_import(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "stack" / "disagg.py").read_text()
        self.assertNotIn(
            "import argparse",
            src,
            "disagg.py must not import argparse — it was only used for a wrong type annotation",
        )

    def test_deployment_accepts_list(self):
        from aictl.stack.disagg import _deployment
        import inspect
        hints = {}
        try:
            hints = _deployment.__annotations__
        except AttributeError:
            pass
        args_hint = hints.get("args", None)
        # Should not be argparse.Namespace
        if args_hint is not None:
            import argparse
            self.assertIsNot(
                args_hint,
                argparse.Namespace,
                "_deployment() 'args' parameter must not be typed as argparse.Namespace; "
                "it receives a list[str] of container command arguments",
            )

    def test_deployment_generates_valid_manifest(self):
        from aictl.stack.disagg import _deployment
        manifest = _deployment(
            name="test-deploy",
            image="vllm/vllm-openai:latest",
            args=["--model", "meta-llama/Meta-Llama-3-8B"],
            replicas=1,
            gpu=1,
            namespace="default",
            port=8000,
            labels={"role": "prefill"},
        )
        self.assertEqual(manifest["kind"], "Deployment")
        containers = manifest["spec"]["template"]["spec"]["containers"]
        self.assertEqual(containers[0]["args"], ["--model", "meta-llama/Meta-Llama-3-8B"])


if __name__ == "__main__":
    unittest.main()

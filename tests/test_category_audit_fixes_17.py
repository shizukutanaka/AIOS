"""Pass 17 regression tests for correctness bugs identified by deep audit."""

import unittest


class TestRouteAskReturnsOnOnInferenceFailure(unittest.TestCase):
    """route.py run_ask: an inference exception must return rc=1, not rc=0."""

    def test_ask_inference_failure_returns_1(self):
        import argparse
        import io
        from contextlib import redirect_stdout
        from aictl.cmd.route import run_ask

        # Force an exception during inference by using a nonsense prompt and
        # ensuring the SDK raises. We patch the SDK import path to raise.
        import sys
        import types

        # Create a mock aictl module that raises on ai.ask()
        mock_aictl = types.ModuleType("aictl")
        mock_ai = types.SimpleNamespace(ask=lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("endpoint unreachable")))
        mock_aictl.ai = mock_ai

        import aictl.sdk as sdk_mod

        original_reset = None
        original_aictl = sys.modules.get("aictl")
        original_context = sdk_mod.__dict__.get("_AmbientContext")

        class _FakeCtx:
            @staticmethod
            def reset_for_testing():
                pass

        try:
            sys.modules["aictl"] = mock_aictl
            # Patch _AmbientContext so reset_for_testing doesn't fail
            sdk_mod._AmbientContext = _FakeCtx

            args = argparse.Namespace(
                prompt="hello", json=False, state_dir=None)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_ask(args)

            self.assertEqual(
                rc, 1,
                "run_ask must return 1 when inference raises an exception, "
                "not 0 (which would falsely signal success to the caller)",
            )
        finally:
            if original_aictl is not None:
                sys.modules["aictl"] = original_aictl
            else:
                sys.modules.pop("aictl", None)
            if original_context is not None:
                sdk_mod._AmbientContext = original_context

    def test_ask_source_code_has_return_1_in_except(self):
        import pathlib, re
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "cmd" / "route.py").read_text()
        # The fix: except block must end with return 1 (not fall through to return 0)
        match = re.search(
            r'except Exception.*?:\s*\n\s+warn\([^)]+\)\s*\n\s+return 1',
            src, re.DOTALL)
        self.assertIsNotNone(
            match,
            "route.py run_ask except-block must return 1 after logging the warning",
        )


if __name__ == "__main__":
    unittest.main()

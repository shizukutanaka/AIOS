"""Pass 39 regression tests: TrustPolicy mode validation, TRT-LLM model arg, apikeys timing."""

import pathlib
import unittest


class TestTrustPolicyModeValidation(unittest.TestCase):
    """TrustPolicy must reject invalid mode strings at construction time."""

    def test_valid_modes_accepted(self):
        """TrustPolicy must accept 'enforce', 'warn', 'disabled'."""
        from aictl.trust.verify import TrustPolicy
        for mode in ("enforce", "warn", "disabled"):
            try:
                TrustPolicy(mode)
            except ValueError:
                self.fail(f"TrustPolicy({mode!r}) raised ValueError unexpectedly")

    def test_invalid_mode_raises(self):
        """TrustPolicy must raise ValueError for unknown modes (e.g., typos)."""
        from aictl.trust.verify import TrustPolicy
        for bad_mode in ("enfoce", "Enforce", "WARN", "", "off", "on"):
            with self.assertRaises(ValueError,
                                   msg=f"TrustPolicy({bad_mode!r}) should raise ValueError"):
                TrustPolicy(bad_mode)

    def test_typo_in_enforce_rejects_not_bypasses(self):
        """Typo 'enfoce' must not silently become warn mode and pass bad models."""
        from aictl.trust.verify import TrustPolicy
        with self.assertRaises(ValueError):
            p = TrustPolicy("enfoce")
            # If this got here, check() would silently allow mismatches
            import tempfile, os
            with tempfile.NamedTemporaryFile(delete=False) as f:
                f.write(b"model data")
                tmp = f.name
            try:
                ok, msg = p.check(tmp, "sha256:deadbeef")
                self.assertFalse(ok, "Typo mode must not pass mismatched digest")
            finally:
                os.unlink(tmp)

    def test_enforce_mode_rejects_mismatch(self):
        """enforce mode must return False for digest mismatch."""
        from aictl.trust.verify import TrustPolicy
        import tempfile, os
        p = TrustPolicy("enforce")
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"some model data")
            tmp = f.name
        try:
            ok, msg = p.check(tmp, "sha256:0000000000000000000000000000000000000000000000000000000000000000")
            self.assertFalse(ok, "enforce mode must reject mismatched digest")
        finally:
            os.unlink(tmp)


class TestOrchestratorTrtLlmModelArg(unittest.TestCase):
    """orchestrator.py must include model arg for trt-llm runtime."""

    def test_orchestrator_has_trt_llm_branch(self):
        """orchestrator.py must have trt-llm in the model_arg conditions."""
        src = (
            pathlib.Path(__file__).parent.parent
            / "aictl" / "stack" / "orchestrator.py"
        ).read_text()
        self.assertIn(
            "trt-llm",
            src,
            "orchestrator.py must handle trt-llm runtime for model arg.",
        )

    def test_trt_llm_model_arg_not_empty(self):
        """model_arg logic must assign non-empty value for trt-llm with a model."""
        src = (
            pathlib.Path(__file__).parent.parent
            / "aictl" / "stack" / "orchestrator.py"
        ).read_text()
        # The trt-llm branch must set model_arg to a list with --model
        self.assertIn(
            'svc.runtime == "trt-llm"',
            src,
            'orchestrator.py must have elif svc.runtime == "trt-llm" branch.',
        )


class TestApiKeyConstantTimeComparison(unittest.TestCase):
    """apikeys.py must use constant-time hash comparison."""

    def test_uses_secrets_compare_digest(self):
        """apikeys.py must use secrets.compare_digest for key hash comparison."""
        src = (
            pathlib.Path(__file__).parent.parent
            / "aictl" / "core" / "apikeys.py"
        ).read_text()
        self.assertIn(
            "secrets.compare_digest",
            src,
            "apikeys.py must use secrets.compare_digest() for timing-safe key hash comparison.",
        )

    def test_no_direct_equality_on_key_hash(self):
        """apikeys.py must not use == to compare key_hash strings."""
        src = (
            pathlib.Path(__file__).parent.parent
            / "aictl" / "core" / "apikeys.py"
        ).read_text()
        self.assertNotIn(
            'kdata.get("key_hash") == key_hash',
            src,
            "apikeys.py still uses == for key_hash comparison — replace with secrets.compare_digest().",
        )

    def test_key_validation_works(self):
        """Key validation must still work correctly with constant-time comparison."""
        import tempfile
        from pathlib import Path
        from aictl.core.apikeys import KeyManager

        with tempfile.TemporaryDirectory() as td:
            mgr = KeyManager(Path(td))
            raw_key, key_obj = mgr.generate_key("test-service")

            valid, reason, found = mgr.validate(raw_key)
            self.assertTrue(valid, f"Valid key rejected: {reason}")
            self.assertEqual(reason, "Valid")

            valid2, reason2, _ = mgr.validate("aios-badkey000000000000000000000000000000000")
            self.assertFalse(valid2, "Bad key should be rejected")


if __name__ == "__main__":
    unittest.main()

"""Pass 23/24 regression tests: dynamo kvbm, cosign method, verify FileNotFoundError."""

import unittest


class TestDynamoKvbmDetection(unittest.TestCase):
    """dynamo.py detect_dynamo: kvbm_available must reflect actual detection, not always False."""

    def test_kvbm_key_present_in_result(self):
        from aictl.runtime.dynamo import detect_dynamo
        result = detect_dynamo()
        self.assertIn("kvbm_available", result, "detect_dynamo result must include kvbm_available key")

    def test_kvbm_is_bool(self):
        from aictl.runtime.dynamo import detect_dynamo
        result = detect_dynamo()
        self.assertIsInstance(result["kvbm_available"], bool)

    def test_kvbm_detection_code_exists(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "runtime" / "dynamo.py").read_text()
        self.assertIn(
            "libkvbm",
            src,
            "detect_dynamo must check for KVBM library paths, not hardcode False",
        )


class TestCosignAttestationUnavailableMethod(unittest.TestCase):
    """cosign.py verify_attestation: must set method='cosign-unavailable' when cosign absent."""

    def test_verify_attestation_sets_unavailable_method(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "trust" / "cosign.py").read_text()
        import re
        fn = re.search(r"def verify_attestation\(.*?^def ", src, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(fn, "verify_attestation must exist")
        body = fn.group(0)
        self.assertIn(
            '"cosign-unavailable"',
            body,
            "verify_attestation must set method='cosign-unavailable' when cosign is not installed",
        )

    def test_verify_attestation_unavailable_consistent_with_verify_image(self):
        """Both verify_image and verify_attestation must use 'cosign-unavailable' when absent."""
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "trust" / "cosign.py").read_text()
        count = src.count('"cosign-unavailable"')
        self.assertGreaterEqual(
            count, 2,
            "Both verify_image and verify_attestation must set method='cosign-unavailable'",
        )


class TestVerifyTrustPolicyFileNotFound(unittest.TestCase):
    """verify.py TrustPolicy.check: missing file must return (bool, msg) not raise."""

    def test_missing_file_warn_mode_does_not_raise(self):
        from aictl.trust.verify import TrustPolicy
        policy = TrustPolicy(mode="warn")
        ok, msg = policy.check("/nonexistent/path/model.gguf", "sha256:abc123")
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(msg, str)
        self.assertIn("file not found", msg.lower())

    def test_missing_file_enforce_mode_returns_false(self):
        from aictl.trust.verify import TrustPolicy
        policy = TrustPolicy(mode="enforce")
        ok, msg = policy.check("/nonexistent/path/model.gguf", "sha256:abc123")
        self.assertFalse(ok, "enforce mode must reject when file is missing")
        self.assertIn("file not found", msg.lower())

    def test_missing_file_warn_mode_returns_true(self):
        from aictl.trust.verify import TrustPolicy
        policy = TrustPolicy(mode="warn")
        ok, msg = policy.check("/nonexistent/path/model.gguf", "sha256:abc123")
        self.assertTrue(ok, "warn mode must still return True (with warning) when file missing")


if __name__ == "__main__":
    unittest.main()

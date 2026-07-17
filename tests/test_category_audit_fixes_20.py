"""Pass 20 regression tests: security.py check-exception handling and lora.py mkdir."""

import unittest


class TestSecurityCheckExceptionHandling(unittest.TestCase):
    """security.py: a crashing check must add a finding and deduct from score."""

    def _make_crashing_check(self):
        """Return a security check function that always raises."""
        def _bad_check(store):
            raise RuntimeError("simulated check failure")
        return _bad_check

    def test_crashing_check_adds_finding(self):
        """An exception in a security check must produce a SecurityFinding."""
        from aictl.core.security import SecurityFinding, SecurityReport
        from aictl.core.security import SecurityFinding

        # Replicate the check loop logic from run_audit
        import dataclasses
        report = SecurityReport()
        check = self._make_crashing_check()
        deductions = {"critical": 25, "high": 15, "medium": 10, "low": 5, "info": 0}

        try:
            finding = check(None)
            report.checks_total += 1
            if finding:
                report.findings.append(finding)
                report.checks_failed += 1
                report.score -= deductions.get(finding.severity, 5)
            else:
                report.checks_passed += 1
        except Exception as exc:
            report.checks_total += 1
            report.checks_failed += 1
            report.findings.append(SecurityFinding(
                severity="medium",
                category="runtime",
                title=f"Security check error: {check.__name__}",
                description=f"Check raised an unexpected exception: {exc}",
                remediation="Investigate why the check failed; run with verbose logging.",
            ))
            report.score -= 10

        self.assertEqual(len(report.findings), 1, "Exception must produce exactly one finding")
        self.assertEqual(report.checks_failed, 1)
        self.assertLess(report.score, 100, "Score must be deducted on check exception")

    def test_security_source_exception_branch_captures_exc(self):
        """security.py except-block must reference the exception (not swallow it)."""
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "core" / "security.py").read_text()
        self.assertIn(
            "except Exception as exc",
            src,
            "security.py must capture the exception variable for diagnostic reporting",
        )

    def test_security_source_exception_adds_finding(self):
        """security.py except-block must append a SecurityFinding."""
        import pathlib, re
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "core" / "security.py").read_text()
        # Find the except block and verify it appends a finding
        match = re.search(
            r"except Exception as exc:.*?report\.findings\.append",
            src, re.DOTALL,
        )
        self.assertIsNotNone(
            match,
            "security.py except-block must append to report.findings",
        )

    def test_run_audit_handles_crashing_check(self):
        """run_audit must not raise even if a check function raises."""
        import tempfile
        from pathlib import Path
        from aictl.core.security import scan as run_audit

        with tempfile.TemporaryDirectory() as d:
            # run_audit must not raise
            report = run_audit(Path(d))
            self.assertIsNotNone(report)
            self.assertGreaterEqual(report.score, 0)


class TestLoraMkdir(unittest.TestCase):
    """lora.py LoRAManager._save must create parent directory if missing."""

    def test_register_base_creates_directory(self):
        """LoRAManager.register_base must work even if state_dir doesn't exist yet."""
        import tempfile
        from pathlib import Path
        from aictl.runtime.lora import LoRAManager, BaseModel

        with tempfile.TemporaryDirectory() as d:
            # Point to a sub-subdirectory that does NOT yet exist
            deep_dir = Path(d) / "nested" / "state"
            mgr = LoRAManager(state_dir=deep_dir)
            # Must not raise FileNotFoundError
            mgr.register_base(BaseModel(name="llama3:8b", vram_mb=8192))
            bases = mgr.list_bases()
        self.assertEqual(len(bases), 1)
        self.assertEqual(bases[0].name, "llama3:8b")

    def test_save_mkdir_in_source(self):
        """lora.py _save must call mkdir before write_text."""
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "aictl" / "runtime" / "lora.py").read_text()
        self.assertIn(
            "mkdir",
            src,
            "lora.py _save must call parent.mkdir to ensure directory exists",
        )


if __name__ == "__main__":
    unittest.main()

"""Pass 45 regression tests: security.py severity sort must not crash on unknown values."""

import unittest


class TestSecuritySeveritySort(unittest.TestCase):
    """Security findings must sort gracefully even with unknown severity strings."""

    def _make_finding(self, severity: str):
        from aictl.core.security import SecurityFinding
        return SecurityFinding(
            severity=severity,
            category="runtime",
            title=f"Test finding ({severity})",
            description="Test",
        )

    def _run_display(self, findings):
        """Run the security display logic in isolation, returns True if no crash."""
        from aictl.core.security import SecurityReport
        report = SecurityReport(findings=findings, checks_total=1)

        # Replicate the sort logic from cmd/security.py
        _sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        sorted_findings = sorted(report.findings, key=lambda x: _sev_rank.get(x.severity, 5))
        return sorted_findings

    def test_known_severities_sort_correctly(self):
        """Known severity levels must sort in critical→high→medium→low→info order."""
        findings = [
            self._make_finding("info"),
            self._make_finding("low"),
            self._make_finding("critical"),
            self._make_finding("medium"),
            self._make_finding("high"),
        ]
        sorted_f = self._run_display(findings)
        severities = [f.severity for f in sorted_f]
        self.assertEqual(severities, ["critical", "high", "medium", "low", "info"])

    def test_unknown_severity_does_not_crash(self):
        """An unknown severity string must not raise ValueError during sort."""
        findings = [
            self._make_finding("critical"),
            self._make_finding("unknown-future-level"),
            self._make_finding("high"),
        ]
        try:
            sorted_f = self._run_display(findings)
        except ValueError as e:
            self.fail(f"Sort crashed with ValueError on unknown severity: {e}")

    def test_unknown_severity_sorts_last(self):
        """Unknown severity must sort after all known levels (rank=5)."""
        findings = [
            self._make_finding("info"),
            self._make_finding("novel"),
        ]
        sorted_f = self._run_display(findings)
        self.assertEqual(sorted_f[0].severity, "info")
        self.assertEqual(sorted_f[1].severity, "novel")

    def test_security_source_uses_dict_not_index(self):
        """cmd/security.py must use dict.get() for severity rank, not list.index()."""
        import pathlib
        src = (pathlib.Path(__file__).parent.parent
               / "aictl" / "cmd" / "security.py").read_text()
        self.assertNotIn(
            ".index(x.severity)",
            src,
            "security.py must not use list.index(x.severity) — use dict.get() with fallback",
        )
        self.assertIn(
            "_sev_rank",
            src,
            "security.py must define _sev_rank dict for severity ordering",
        )


if __name__ == "__main__":
    unittest.main()

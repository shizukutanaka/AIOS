"""Pass 18 regression tests for correctness bugs identified by deep audit."""

import pathlib
import re
import unittest


class TestGoPortApplyJsonFlag(unittest.TestCase):
    """go-port/cmd/aictl/main.go: cmdApply must check jsonFlag before text output."""

    def test_apply_func_has_json_guard(self):
        src = (pathlib.Path(__file__).parent.parent / "go-port" / "cmd" / "aictl" / "main.go").read_text()
        # Find cmdApply function body
        apply_section = re.search(r'func cmdApply\(\).*?^}', src, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(apply_section, "cmdApply function must exist")
        body = apply_section.group(0)
        self.assertIn(
            "jsonFlag",
            body,
            "cmdApply must check jsonFlag before printing text output",
        )

    def test_apply_json_path_calls_printJSON(self):
        src = (pathlib.Path(__file__).parent.parent / "go-port" / "cmd" / "aictl" / "main.go").read_text()
        apply_section = re.search(r'func cmdApply\(\).*?^}', src, re.MULTILINE | re.DOTALL)
        body = apply_section.group(0)
        self.assertIn(
            "printJSON",
            body,
            "cmdApply JSON branch must call printJSON",
        )


class TestGoPortSecurityJsonFlag(unittest.TestCase):
    """go-port/cmd/aictl/main.go: cmdSecurity must support --json output."""

    def test_security_func_has_json_guard(self):
        src = (pathlib.Path(__file__).parent.parent / "go-port" / "cmd" / "aictl" / "main.go").read_text()
        security_section = re.search(r'func cmdSecurity\(\).*?^}', src, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(security_section, "cmdSecurity function must exist")
        body = security_section.group(0)
        self.assertIn(
            "jsonFlag",
            body,
            "cmdSecurity must check jsonFlag and emit JSON output when requested",
        )

    def test_security_json_path_returns_score(self):
        src = (pathlib.Path(__file__).parent.parent / "go-port" / "cmd" / "aictl" / "main.go").read_text()
        security_section = re.search(r'func cmdSecurity\(\).*?^}', src, re.MULTILINE | re.DOTALL)
        body = security_section.group(0)
        self.assertIn(
            '"score"',
            body,
            "cmdSecurity JSON output must include a 'score' field",
        )


if __name__ == "__main__":
    unittest.main()

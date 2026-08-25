"""Pass 221: the documented cost example was an output the code could not produce.

Found by running the README's own quick start rather than reading it. Every CLI
command worked. The Python snippet did not:

    print(r.cost)      # "$0.000047"

`_Response.cost` rendered anything under $0.0001 as `f"${cost_usd * 1000:.4f}m"`
— millidollars with a bare `m` suffix — so 0.000047 came out as **"$0.0470m"**.
The documented output was unreachable for the documented value.

Three consequences, worsening downward:

  * The unit is ambiguous. "$0.0470m" reads as millions at least as readily as
    thousandths, and the second copy of this formula (`core/cost_per_call.py`)
    commented it "millicents", which it is not — two call sites that disagreed
    about what their own number meant.
  * A genuinely free response printed **"$0.0000m"**. That is the in-process
    mock, which is exactly what zero-config gives a first-time user, so the
    malformed string was the most likely thing anyone would ever see.
  * `aictl route cascade` summed costs by string concatenation. `total_cost`
    was initialised as a float, reassigned to `r1.cost` (a *string*), then
    `total_cost += r2.cost`. An escalating cascade reported
    **"$0.0000m$0.0000m"** — in the `--json` payload a script would parse as
    well as on screen. Reproduced before fixing.

One formatter now, in dollars, with no suffix to misread; six decimals cover
everything the milli form did. Below display resolution it says "<$0.000001"
rather than rounding a real cost to a confident zero. Costs are accumulated
numerically and formatted once at the boundary, and the JSON payload gained
`total_cost_usd` so callers never have to parse a currency string back.
"""

from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from aictl.core.cost_per_call import COST_DISPLAY_FLOOR, format_usd


class TestCostFormatting(unittest.TestCase):
    def test_the_readme_example_is_reachable(self):
        # The specific number the README prints. It rendered as "$0.0470m".
        self.assertEqual(format_usd(0.000047), "$0.000047")

    def test_free_is_not_malformed(self):
        # Was "$0.0000m" — and this is the zero-config mock path, so it was
        # the most likely output any new user would see.
        self.assertEqual(format_usd(0.0), "$0.000000")

    def test_negative_is_treated_as_free(self):
        self.assertEqual(format_usd(-1.0), "$0.000000")

    def test_cached_is_labelled(self):
        self.assertEqual(format_usd(0.0025, cached=True), "$0.000000 (cached)")

    def test_ordinary_cost_uses_six_decimals(self):
        self.assertEqual(format_usd(0.0025), "$0.002500")
        self.assertEqual(format_usd(1.5), "$1.500000")

    def test_below_resolution_says_so_rather_than_rounding_to_zero(self):
        # Rounding a real cost to $0.000000 would claim it was free.
        self.assertEqual(format_usd(COST_DISPLAY_FLOOR / 10), "<$0.000001")

    def test_no_ambiguous_suffix_anywhere(self):
        for value in (0.0, 4e-7, 4.7e-5, 0.0025, 1.5, 1234.5):
            self.assertFalse(format_usd(value).endswith("m"),
                             f"{value} still renders with the milli suffix")

    def test_every_output_starts_with_a_currency_marker(self):
        for value in (0.0, 4e-7, 4.7e-5, 1.5):
            self.assertRegex(format_usd(value), r"^[<$]")


class TestOneFormatterNotTwo(unittest.TestCase):
    """Two copies of this formula disagreed about their own unit."""

    def test_sdk_delegates_rather_than_reimplementing(self):
        source = Path("aictl/sdk.py").read_text()
        self.assertIn("format_usd", source)
        self.assertNotIn('* 1000:.4f}m', source)

    def test_cost_per_call_delegates_too(self):
        from aictl.core.cost_per_call import compute, format_cost

        cost = compute("local", 1000, 500, is_local=True)
        self.assertEqual(format_cost(cost), format_usd(cost.cost_usd))

    def test_the_milli_formula_is_gone_from_executable_code(self):
        # Checked by parsing, with docstrings stripped, not by grepping.
        # The first version of this test asserted "millicents" was absent from
        # the file and failed on the docstring that *explains* the wrong unit
        # — the sixth substring check this session to catch prose instead of
        # behaviour. Both modules legitimately discuss the old formula in
        # prose; what must not survive is the formula itself.
        import ast

        for module in ("aictl/sdk.py", "aictl/core/cost_per_call.py"):
            tree = ast.parse(Path(module).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                    continue                      # a docstring; not code
                if isinstance(node, ast.JoinedStr):
                    rendered = ast.unparse(node)
                    self.assertNotIn("* 1000", rendered,
                                     f"{module} still formats millidollars")

    def test_jpy_is_unaffected(self):
        from aictl.core.cost_per_call import compute, format_cost

        cost = compute("local", 1000, 500, is_local=True)
        self.assertTrue(format_cost(cost, currency="jpy").startswith("¥"))


class TestSdkResponseCost(unittest.TestCase):
    def test_response_cost_is_a_well_formed_string(self):
        import aictl

        r = aictl.ai.ask("hello")
        self.assertRegex(r.cost, r"^[<$]")
        self.assertFalse(r.cost.endswith("m"))

    def test_mock_response_reports_free_not_garbage(self):
        import aictl

        r = aictl.ai.ask("hello")
        if getattr(r, "mock", False):
            self.assertEqual(r.cost, "$0.000000")


class TestCascadeSumsNumerically(unittest.TestCase):
    """The bug that shipped in --json as well as on screen."""

    def _cascade(self, min_length: int) -> dict:
        from aictl.cmd.route import run_cascade

        namespace = argparse.Namespace(prompt="Explain quantum entanglement",
                                       json=True, model=None,
                                       min_length=min_length)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            run_cascade(namespace)
        return json.loads(buffer.getvalue())

    def test_escalated_cost_is_not_two_strings_glued_together(self):
        payload = self._cascade(100000)     # forces escalation
        self.assertTrue(payload["escalated"])
        self.assertEqual(payload["total_cost"].count("$"), 1,
                         f"concatenated price tags: {payload['total_cost']!r}")

    def test_json_carries_a_numeric_cost(self):
        # So a caller never has to parse a currency string back into a number.
        payload = self._cascade(100000)
        self.assertIsInstance(payload["total_cost_usd"], (int, float))

    def test_the_two_cost_fields_agree(self):
        payload = self._cascade(100000)
        self.assertEqual(payload["total_cost"],
                         format_usd(payload["total_cost_usd"]))

    def test_direct_path_also_reports_one_price(self):
        payload = self._cascade(1)          # no escalation
        self.assertEqual(payload["total_cost"].count("$"), 1)

    def test_route_accumulates_the_numeric_field(self):
        source = Path("aictl/cmd/route.py").read_text()
        self.assertIn("total_cost += r2.cost_usd", source)
        self.assertNotIn("total_cost += r2.cost\n", source)


class TestTheDocumentedQuickStartRuns(unittest.TestCase):
    """The README's snippet, executed rather than read."""

    def test_ask_accepts_the_documented_keyword(self):
        import aictl

        r = aictl.ai.ask("Summarize this", context="Refunds within 30 days.")
        self.assertTrue(str(r))

    def test_the_documented_attributes_exist(self):
        import aictl

        r = aictl.ai.ask("hello")
        for attr in ("cost", "cached"):
            self.assertTrue(hasattr(r, attr), f"README documents r.{attr}")


if __name__ == "__main__":
    unittest.main()

"""Pass 202: advisory review of speculative-decoding tuning.

`generate_*_args` validated `num_speculative_tokens > 0` and nothing else, so
a config of 20 draft tokens was accepted silently. Published tuning guidance
puts the useful band at roughly 3-8: below the floor throughput is left
unclaimed, and above the ceiling the expected accepted tokens per step
plateaus, so the extra draft compute produces tokens that are almost never all
accepted. Paying for drafts nobody accepts is the exact failure speculative
decoding exists to avoid, and it is invisible — it shows up as a
disappointing speedup, not an error.

Also surfaced: an EAGLE3 draft head is trained on one target model's own
generations. Pointed at a fine-tune of that model it drafts in the wrong
style and acceptance falls, again silently.

These are advisory, not validation. `review_config` returns notes;
`generate_*_args` still only rejects values <= 0, because a deployment with a
measured reason to sit outside the band must not be blocked by a heuristic.
`estimate_speedup`'s existing figures were checked against the same sources
and left alone — they are conservative rather than over-promising.
"""

from __future__ import annotations

import unittest

from aictl.runtime.speculative import (
    SPEC_TOKENS_MAX,
    SPEC_TOKENS_MIN,
    SpeculativeConfig,
    auto_select_method,
    estimate_speedup,
    generate_vllm_args,
    review_config,
)


class TestTokenBandAdvice(unittest.TestCase):
    def test_above_the_ceiling_is_flagged(self):
        notes = review_config(SpeculativeConfig(method="ngram",
                                                num_speculative_tokens=20))
        self.assertTrue(any("exceeds" in n for n in notes))

    def test_below_the_floor_is_flagged(self):
        notes = review_config(SpeculativeConfig(method="ngram",
                                                num_speculative_tokens=1))
        self.assertTrue(any("below" in n for n in notes))

    def test_inside_the_band_is_silent(self):
        for tokens in range(SPEC_TOKENS_MIN, SPEC_TOKENS_MAX + 1):
            notes = review_config(SpeculativeConfig(method="ngram",
                                                    num_speculative_tokens=tokens))
            self.assertEqual(notes, [], f"{tokens} tokens should not be flagged")

    def test_boundaries_are_inclusive(self):
        # The band edges are usable values, not the first bad ones.
        for tokens in (SPEC_TOKENS_MIN, SPEC_TOKENS_MAX):
            self.assertEqual(
                review_config(SpeculativeConfig(method="ngram",
                                                num_speculative_tokens=tokens)), [])

    def test_disabled_speculation_is_never_flagged(self):
        # method="none" ignores the token count entirely.
        notes = review_config(SpeculativeConfig(method="none",
                                                num_speculative_tokens=999))
        self.assertEqual(notes, [])

    def test_advice_names_the_actual_value(self):
        notes = review_config(SpeculativeConfig(method="ngram",
                                                num_speculative_tokens=42))
        self.assertTrue(any("42" in n for n in notes))


class TestFineTuneCaveat(unittest.TestCase):
    def test_eagle3_with_a_draft_model_carries_the_caveat(self):
        notes = review_config(SpeculativeConfig(
            method="eagle3", draft_model="some/eagle3-head",
            num_speculative_tokens=5))
        self.assertTrue(any("fine-tune" in n for n in notes))

    def test_ngram_does_not_carry_it(self):
        # N-gram drafts from the prompt itself; there is no draft model whose
        # training distribution could mismatch.
        notes = review_config(SpeculativeConfig(method="ngram",
                                                num_speculative_tokens=5))
        self.assertFalse(any("fine-tune" in n for n in notes))

    def test_mtp_does_not_carry_it(self):
        # Native MTP heads ship with the target model, so they cannot mismatch.
        notes = review_config(SpeculativeConfig(method="mtp",
                                                num_speculative_tokens=3))
        self.assertFalse(any("fine-tune" in n for n in notes))


class TestAdviceIsNotValidation(unittest.TestCase):
    """A heuristic must not block a deployment that has measured otherwise."""

    def test_out_of_band_config_still_generates_args(self):
        args = generate_vllm_args(SpeculativeConfig(method="ngram",
                                                    num_speculative_tokens=20))
        self.assertTrue(args)

    def test_zero_tokens_is_still_a_hard_error(self):
        with self.assertRaises(ValueError):
            generate_vllm_args(SpeculativeConfig(method="ngram",
                                                 num_speculative_tokens=0))

    def test_review_never_raises(self):
        for tokens in (-5, 0, 1, 5, 1000):
            for method in ("none", "ngram", "mtp", "eagle3", "medusa"):
                review_config(SpeculativeConfig(method=method, draft_model="d",
                                                num_speculative_tokens=tokens))


class TestEstimateSpeedupShape(unittest.TestCase):
    def test_warnings_key_always_present(self):
        # Stable shape for --json consumers: empty means nothing to flag,
        # never "not reviewed".
        for method in ("none", "ngram", "mtp", "eagle3"):
            result = estimate_speedup(SpeculativeConfig(
                method=method, draft_model="d", num_speculative_tokens=5))
            self.assertIn("warnings", result)
            self.assertIsInstance(result["warnings"], list)

    def test_existing_keys_are_unchanged(self):
        result = estimate_speedup(SpeculativeConfig(method="ngram",
                                                    num_speculative_tokens=5))
        for key in ("method", "estimated_latency_speedup",
                    "estimated_throughput_speedup", "draft_model", "note"):
            self.assertIn(key, result)

    def test_speedup_estimates_stay_conservative(self):
        # Published figures reach 2-3x; ours must not claim more than measured
        # guidance supports, so over-promising is caught if someone inflates them.
        for method in ("ngram", "mtp", "eagle3"):
            result = estimate_speedup(SpeculativeConfig(
                method=method, draft_model="d", num_speculative_tokens=5))
            self.assertLessEqual(result["estimated_throughput_speedup"], 3.0)
            self.assertGreaterEqual(result["estimated_latency_speedup"], 1.0)


class TestAutoSelectedConfigsAreClean(unittest.TestCase):
    """aictl's own defaults must not trip its own advice."""

    def test_auto_selected_methods_produce_no_warnings(self):
        for model in ("meta-llama/Llama-3.1-8B", "DeepSeek-R1", "DeepSeek-V3",
                      "Qwen3-32B", "unknown/model"):
            config = auto_select_method(model)
            band_notes = [n for n in review_config(config)
                          if "exceeds" in n or "below" in n]
            self.assertEqual(band_notes, [],
                             f"auto-selected config for {model} is outside the band")

    def test_auto_selected_token_counts_are_in_band(self):
        for model in ("meta-llama/Llama-3.1-8B", "DeepSeek-R1", "unknown/model"):
            tokens = auto_select_method(model).num_speculative_tokens
            self.assertGreaterEqual(tokens, SPEC_TOKENS_MIN)
            self.assertLessEqual(tokens, SPEC_TOKENS_MAX)



class TestSpecCommandSurfacesWarnings(unittest.TestCase):
    """Advice nobody can read is the same as no advice.

    Pass 202 added `warnings` to estimate_speedup, but `spec methods` builds
    its JSON payload key-by-key and prints a fixed set of lines, so the new
    key was silently dropped in both modes — the same "built but unreachable"
    pattern item W caught for the KV offload advisor.
    """

    def _run(self, model, use_json):
        import argparse
        import io
        import json as _json
        from contextlib import redirect_stdout

        from aictl.cmd.spec import run_methods

        namespace = argparse.Namespace(model=model, all=False, json=use_json)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = run_methods(namespace)
        self.assertEqual(code, 0)
        output = buffer.getvalue()
        return _json.loads(output) if use_json else output

    def test_json_payload_carries_warnings(self):
        payload = self._run("meta-llama/Llama-3.3-70B-Instruct", True)
        self.assertIn("warnings", payload)
        self.assertIsInstance(payload["warnings"], list)

    def test_json_warnings_present_for_an_eagle3_model(self):
        payload = self._run("meta-llama/Llama-3.3-70B-Instruct", True)
        self.assertTrue(any("fine-tune" in w for w in payload["warnings"]))

    def test_json_warnings_empty_for_ngram_fallback(self):
        # N-gram has no draft model, so the caveat must not appear as noise.
        payload = self._run("definitely/not-a-known-model-7b", True)
        self.assertEqual(payload["warnings"], [])

    def test_text_output_shows_the_caveat(self):
        output = self._run("meta-llama/Llama-3.3-70B-Instruct", False)
        self.assertIn("fine-tune", output)

    def test_text_output_stays_quiet_for_ngram(self):
        output = self._run("definitely/not-a-known-model-7b", False)
        self.assertNotIn("fine-tune", output)

if __name__ == "__main__":
    unittest.main()

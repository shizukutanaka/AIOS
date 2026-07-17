"""Pass 184 (IMPROVEMENTS.md items Q + R): catalog + advisor refresh from the
July 2026 research pass.

- Item R (catalog drift): GLM-5.2 (June 2026, leading open-weights on the
  Artificial Analysis index) and Kimi K2.6 (April 2026, long-horizon
  agentic) added to runtime/recommend.py's MODELS (34 -> 37 rows; the
  count-sync pin in test_category_audit_fixes_32.py and
  runtime/CLAUDE.md's "N models in DB" line move together with it).
- Item R (Medusa): the one remaining gap from item L -- "medusa" is now a
  modeled method in runtime/speculative.py's estimate_speedup and
  cmd/spec.py's _METHOD_INFO matrix (advisory row: engines vLLM + TRT-LLM,
  requires trained Medusa heads, note steers to EAGLE-3 where a head
  exists, per the 2026 acceptance-rate consensus). auto_select_method
  deliberately never picks it -- EAGLE-3 dominates when available.
- Item Q (vLLM v0.19 KV offload): `aictl fit`'s doesn't-fit path now
  mentions CPU KV-cache offloading as a remedy alongside the existing
  quantization/alternative-model suggestions.
"""

from __future__ import annotations

import argparse
import unittest
from unittest.mock import patch


class TestCatalogAdditions(unittest.TestCase):
    def test_glm52_ollama_row_present(self):
        from aictl.runtime.recommend import MODELS
        names = {m.name for m in MODELS}
        self.assertIn("glm5.2:9b", names)

    def test_glm52_vllm_flagship_present(self):
        from aictl.runtime.recommend import MODELS
        names = {m.name for m in MODELS}
        self.assertIn("zai-org/GLM-5.2", names)

    def test_kimi_k26_present(self):
        from aictl.runtime.recommend import MODELS
        names = {m.name for m in MODELS}
        self.assertIn("kimi-k2.6", names)

    def test_new_rows_have_valid_runtime_and_positive_vram(self):
        from aictl.runtime.recommend import MODELS
        new = [m for m in MODELS
               if m.name in ("glm5.2:9b", "zai-org/GLM-5.2", "kimi-k2.6")]
        self.assertEqual(len(new), 3)
        for m in new:
            self.assertIn(m.runtime, ("ollama", "vllm", "sglang"))
            self.assertGreater(m.vram_required_mb, 0)
            self.assertGreater(m.context_length, 0)

    def test_count_pin_and_docs_stay_in_sync(self):
        # Redundant with test_category_audit_fixes_32 but pinned here too so
        # THIS pass's test file fails loudly if the trio ever splits.
        from pathlib import Path
        from aictl.runtime.recommend import MODELS
        claude_md = (Path(__file__).parent.parent / "aictl" / "runtime" / "CLAUDE.md").read_text()
        self.assertIn(f"{len(MODELS)} models", claude_md)


class TestMedusaMethodRow(unittest.TestCase):
    def test_estimate_speedup_knows_medusa(self):
        from aictl.runtime.speculative import SpeculativeConfig, estimate_speedup
        est = estimate_speedup(SpeculativeConfig(method="medusa"))
        self.assertEqual(est["method"], "medusa")
        self.assertGreater(est["estimated_throughput_speedup"], 1.0)
        self.assertIn("EAGLE3", est["note"])

    def test_spec_methods_matrix_includes_medusa(self):
        from aictl.cmd.spec import _METHOD_INFO
        methods = [row[0] for row in _METHOD_INFO]
        self.assertIn("medusa", methods)

    def test_spec_methods_all_json_includes_medusa(self):
        from aictl.cmd.spec import run_methods
        captured = []
        with patch("aictl.core.output.print_json", side_effect=captured.append):
            run_methods(argparse.Namespace(model=None, all=True, json=True))
        methods = {r["method"] for r in captured[0]}
        self.assertIn("medusa", methods)
        # Medusa's row must carry real (non-fallback) speedup numbers --
        # the estimate must not silently fall back to the "none" baseline.
        medusa_row = next(r for r in captured[0] if r["method"] == "medusa")
        self.assertGreater(medusa_row["throughput_speedup"], 1.0)

    def test_auto_select_never_picks_medusa(self):
        # Advisory-only: EAGLE-3 dominates where a head exists, so
        # auto-selection must keep preferring eagle3/mtp/ngram.
        from aictl.runtime.speculative import auto_select_method
        for model in ("llama3.1:8b", "deepseek-v3", "qwen3:7b", "unknown-model-xyz"):
            cfg = auto_select_method(model)
            self.assertNotEqual(cfg.method, "medusa", model)


class TestFitKvOffloadHint(unittest.TestCase):
    def _run_fit(self, model, gpu):
        import io
        import json as _json
        from contextlib import redirect_stdout
        from aictl.cmd import fit as fit_mod
        buf = io.StringIO()
        with redirect_stdout(buf):
            fit_mod.run(argparse.Namespace(
                model=model, gpu=gpu, context=4096, concurrent=1,
                use_case="", json=True))
        return _json.loads(buf.getvalue())

    def test_doesnt_fit_mentions_kv_offloading(self):
        # 70B-class FP16 on an 8GB card: nothing fits -> hint expected.
        result = self._run_fit("llama3.1:70b", "RTX 4060")
        self.assertFalse(result["fits"])
        notes_text = " ".join(result["notes"])
        self.assertIn("KV-cache offloading", notes_text)
        self.assertIn("v0.19", notes_text)

    def test_fits_case_has_no_kv_offload_hint(self):
        result = self._run_fit("llama3.2:1b", "RTX 4090")
        self.assertTrue(result["fits"])
        notes_text = " ".join(result["notes"])
        self.assertNotIn("KV-cache offloading", notes_text)


if __name__ == "__main__":
    unittest.main()

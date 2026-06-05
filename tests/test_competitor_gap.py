"""Tests for competitor-gap features: fit, quant, troubleshoot."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestFit(unittest.TestCase):
    def test_cli_basic(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["fit", "llama3:8b", "--gpu", "H100"])
        self.assertEqual(args.model, "llama3:8b")
        self.assertEqual(args.gpu, "H100")

    def test_cli_with_context_and_concurrent(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args([
            "fit", "qwen3:7b", "--context", "16384", "--concurrent", "4"
        ])
        self.assertEqual(args.context, 16384)
        self.assertEqual(args.concurrent, 4)

    def test_find_model_exact(self):
        from aictl.cmd.fit import _find_model
        from aictl.runtime.recommend import MODELS
        m = _find_model("qwen3:7b", MODELS)
        self.assertIsNotNone(m)

    def test_find_model_dot_normalization(self):
        """llama3-8b should match llama3.1:8b."""
        from aictl.cmd.fit import _find_model
        from aictl.runtime.recommend import MODELS
        m = _find_model("llama3-8b", MODELS)
        self.assertIsNotNone(m)

    def test_find_model_unknown(self):
        from aictl.cmd.fit import _find_model
        from aictl.runtime.recommend import MODELS
        self.assertIsNone(_find_model("nonexistent-xyz-99b", MODELS))

    def test_gpu_vram_lookup(self):
        from aictl.cmd.fit import _lookup_gpu_vram
        self.assertEqual(_lookup_gpu_vram("RTX 4090"), 24576)
        self.assertEqual(_lookup_gpu_vram("H100"), 81920)
        self.assertEqual(_lookup_gpu_vram("B200"), 196608)
        self.assertEqual(_lookup_gpu_vram("unknown"), 0)

    def test_extract_params(self):
        from aictl.cmd.fit import _extract_param_billions
        self.assertEqual(_extract_param_billions("llama3:8b"), 8.0)
        self.assertEqual(_extract_param_billions("qwen3:32b"), 32.0)
        # Default for names without explicit B
        self.assertEqual(_extract_param_billions("deepseek-v4"), 7.0)

    def test_quant_calculation(self):
        from aictl.cmd.fit import _calculate_quantizations
        from aictl.runtime.recommend import MODELS, ModelRec
        m = ModelRec(
            name="test:8b", runtime="vllm",
            vram_required_mb=16000, ram_required_mb=16000,
            use_case="chat", quantization="fp16", context_length=8192,
            notes="test",
        )
        quants = _calculate_quantizations(m, context=8192, concurrent=1)
        # All formats present
        for fmt in ["fp16", "fp8", "q8_0", "awq", "q4_K_M", "q3_K_M"]:
            self.assertIn(fmt, quants)
        # Q4 < FP16
        self.assertLess(quants["q4_K_M"]["weights_mb"], quants["fp16"]["weights_mb"])
        # FP8 ≈ half of FP16
        self.assertAlmostEqual(
            quants["fp8"]["weights_mb"], quants["fp16"]["weights_mb"] * 0.5,
            delta=10,
        )

    def test_first_fit_picks_highest_quality(self):
        from aictl.cmd.fit import _first_fit
        quants = {
            "fp16": {"fits": False}, "fp8": {"fits": False},
            "q8_0": {"fits": True}, "awq": {"fits": True},
            "q4_K_M": {"fits": True}, "q3_K_M": {"fits": True},
        }
        # Should pick q8_0 (highest in priority order that fits)
        self.assertEqual(_first_fit(quants), "q8_0")


class TestQuant(unittest.TestCase):
    def test_cli_recommend(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args([
            "quant", "recommend", "llama3:8b", "--gpu", "H100", "--use-case", "code",
        ])
        self.assertEqual(args.use_case, "code")

    def test_cli_compare(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["quant", "compare", "qwen3:7b"])
        self.assertEqual(args.model, "qwen3:7b")

    def test_data_completeness(self):
        from aictl.cmd.quant import QUANT_DATA
        for fmt in ["fp16", "fp8", "awq", "q4_k_m", "gptq", "q3_k_m"]:
            self.assertIn(fmt, QUANT_DATA)
            d = QUANT_DATA[fmt]
            # Required fields
            for field in ["q_chat", "q_code", "q_reasoning",
                          "size", "engines", "cc", "speed", "notes"]:
                self.assertIn(field, d)

    def test_quality_ordering(self):
        from aictl.cmd.quant import QUANT_DATA
        # FP16 > FP8 > AWQ > Q4 > Q3
        self.assertGreater(
            QUANT_DATA["fp16"]["q_chat"], QUANT_DATA["fp8"]["q_chat"]
        )
        self.assertGreater(
            QUANT_DATA["awq"]["q_chat"], QUANT_DATA["q4_k_m"]["q_chat"]
        )
        self.assertGreater(
            QUANT_DATA["q4_k_m"]["q_chat"], QUANT_DATA["q3_k_m"]["q_chat"]
        )

    def test_size_ordering(self):
        from aictl.cmd.quant import QUANT_DATA
        # FP16 largest, Q3 smallest
        self.assertGreater(QUANT_DATA["fp16"]["size"], QUANT_DATA["fp8"]["size"])
        self.assertGreater(QUANT_DATA["fp8"]["size"], QUANT_DATA["awq"]["size"])
        self.assertGreater(QUANT_DATA["q4_k_m"]["size"], QUANT_DATA["q3_k_m"]["size"])


class TestTroubleshoot(unittest.TestCase):
    def test_cli_basic(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["troubleshoot", "--symptom", "oom"])
        self.assertEqual(args.symptom, "oom")

    def test_cli_default_auto(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["troubleshoot"])
        self.assertEqual(args.symptom, "auto")

    def test_simulate_arg(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        args = p.parse_args(["troubleshoot", "--simulate", "llama3:70b"])
        self.assertEqual(args.simulate, "llama3:70b")

    def test_size_parser(self):
        from aictl.cmd.troubleshoot import _parse_size_mb
        self.assertEqual(_parse_size_mb("4.7 GB"), 4812)  # 4.7*1024
        self.assertEqual(_parse_size_mb("500 MB"), 500)
        self.assertEqual(_parse_size_mb("invalid"), 0)

    def test_auto_detect_returns_empty_with_no_logs(self):
        import os
        import tempfile
        from aictl.cmd.troubleshoot import _detect_symptom_from_logs

        with tempfile.TemporaryDirectory() as td:
            original = os.environ.get("AIOS_STATE_DIR")
            os.environ["AIOS_STATE_DIR"] = td
            try:
                self.assertEqual(_detect_symptom_from_logs(), "")
            finally:
                if original is None:
                    os.environ.pop("AIOS_STATE_DIR", None)
                else:
                    os.environ["AIOS_STATE_DIR"] = original


if __name__ == "__main__":
    unittest.main()

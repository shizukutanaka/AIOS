"""Pass 152: route config must backfill missing tier sub-keys (no KeyError).

`_load_config` restored a *completely missing* tier via `cfg.setdefault(tier,
defaults)`, but NOT a tier dict that exists yet lacks a required sub-key. A
hand-edited `{"medium": {"max_score": 60}}` (no "model") therefore survived load,
and the later `cfg[tier]["model"]` raised KeyError — caught by the top-level
handler and shown as a bogus "Invalid input: model" for a perfectly valid prompt
that happened to score in the MEDIUM band.

Fix: for each tier, replace a missing/non-dict tier with the full default and
otherwise backfill each missing sub-key ("model", "max_score"), so routing never
KeyErrors on a partial/corrupt config.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _with_config(cfg_dict):
    d = tempfile.mkdtemp()
    with patch.dict(os.environ, {"AIOS_STATE_DIR": d}):
        from aictl.cmd.route import _config_path, _load_config
        p = _config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        if cfg_dict is not None:
            p.write_text(json.dumps(cfg_dict))
        return _load_config()


class TestRouteConfigBackfill(unittest.TestCase):
    def test_tier_missing_model_backfilled(self):
        cfg = _with_config({
            "simple": {"model": "a", "max_score": 30},
            "medium": {"max_score": 60},                 # no "model"
            "complex": {"model": "c", "max_score": 100},
        })
        # The whole point: this access used to KeyError.
        self.assertEqual(cfg["medium"]["model"],
                         _default("medium", "model"))

    def test_every_tier_has_model_and_max_score(self):
        cfg = _with_config({"medium": {}, "simple": {}, "complex": {}})
        for tier in ("simple", "medium", "complex"):
            self.assertIn("model", cfg[tier])
            self.assertIn("max_score", cfg[tier])

    def test_non_dict_tier_replaced_with_default(self):
        cfg = _with_config({"simple": None, "medium": "bad",
                            "complex": {"model": "c", "max_score": 100}})
        self.assertEqual(cfg["simple"]["model"], _default("simple", "model"))
        self.assertEqual(cfg["medium"]["model"], _default("medium", "model"))

    def test_completely_missing_tier_restored(self):
        cfg = _with_config({"simple": {"model": "a", "max_score": 30}})
        self.assertIn("complex", cfg)
        self.assertIn("model", cfg["complex"])

    def test_user_model_preserved(self):
        cfg = _with_config({
            "simple": {"model": "my-custom", "max_score": 30},
            "medium": {"max_score": 60},
            "complex": {"model": "c", "max_score": 100},
        })
        self.assertEqual(cfg["simple"]["model"], "my-custom")   # not overwritten

    def test_no_config_uses_defaults(self):
        cfg = _with_config(None)
        for tier in ("simple", "medium", "complex"):
            self.assertIn("model", cfg[tier])


def _default(tier, key):
    from aictl.cmd.route import _DEFAULT_TIERS
    return _DEFAULT_TIERS[tier][key]


class TestRouteEndToEndNoCrash(unittest.TestCase):
    def test_medium_prompt_with_partial_config_routes(self):
        import argparse
        import contextlib
        import io
        d = tempfile.mkdtemp()
        with patch.dict(os.environ, {"AIOS_STATE_DIR": d}):
            from aictl.cmd.route import _config_path, run_show
            p = _config_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({
                "simple": {"model": "a", "max_score": 30},
                "medium": {"max_score": 60},
                "complex": {"model": "c", "max_score": 100},
            }))
            args = argparse.Namespace(
                prompt="Explain how photosynthesis works in plants for a student")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = run_show(args)
            self.assertEqual(code, 0)   # used to raise KeyError -> exit 1


if __name__ == "__main__":
    unittest.main()

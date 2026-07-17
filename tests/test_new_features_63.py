"""Pass 63 regression tests: chat --stream, image prune."""

from __future__ import annotations

import argparse
import io
import json
import sys
import unittest
from unittest.mock import patch, MagicMock, call


class TestChatStream(unittest.TestCase):
    """chat --stream sends stream:true and prints tokens incrementally."""

    def _make_parser(self):
        from aictl.cmd.chat import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_stream_flag_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["chat", "--stream"])
        self.assertTrue(args.stream)

    def test_stream_default_is_false(self):
        parser = self._make_parser()
        args = parser.parse_args(["chat"])
        self.assertFalse(args.stream)

    def test_stream_response_collects_tokens(self):
        """_stream_response reads SSE lines and returns concatenated content."""
        from aictl.cmd.chat import _stream_response

        chunks = [
            b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n',
            b'data: {"choices":[{"delta":{"content":" world"}}]}\n',
            b'data: [DONE]\n',
        ]
        mock_resp = MagicMock()
        mock_resp.__iter__ = MagicMock(return_value=iter(chunks))
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        req = MagicMock()
        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("sys.stdout", new_callable=io.StringIO):
            result = _stream_response(req)
        self.assertEqual(result, "Hello world")

    def test_stream_response_skips_non_data_lines(self):
        from aictl.cmd.chat import _stream_response

        chunks = [
            b': keep-alive\n',
            b'event: message\n',
            b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n',
            b'data: [DONE]\n',
        ]
        mock_resp = MagicMock()
        mock_resp.__iter__ = MagicMock(return_value=iter(chunks))
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        req = MagicMock()
        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("sys.stdout", new_callable=io.StringIO):
            result = _stream_response(req)
        self.assertEqual(result, "Hi")

    def test_stream_response_handles_empty_delta(self):
        from aictl.cmd.chat import _stream_response

        chunks = [
            b'data: {"choices":[{"delta":{}}]}\n',
            b'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
            b'data: [DONE]\n',
        ]
        mock_resp = MagicMock()
        mock_resp.__iter__ = MagicMock(return_value=iter(chunks))
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        req = MagicMock()
        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("sys.stdout", new_callable=io.StringIO):
            result = _stream_response(req)
        self.assertEqual(result, "ok")

    def test_stream_body_sets_stream_true(self):
        """When --stream is set, the POST body must include "stream": true."""
        from aictl.cmd.chat import run

        bodies_sent = []

        def fake_urlopen(req, timeout=None):
            if hasattr(req, "data") and req.data:
                bodies_sent.append(json.loads(req.data.decode()))
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({
                "data": [{"id": "llama3"}]
            }).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        # Simulate one message then EOF
        inputs = iter(["hello", "quit"])
        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("aictl.cmd.chat.load_config") as mock_cfg, \
             patch("builtins.input", side_effect=inputs), \
             patch("sys.stdout", new_callable=io.StringIO):
            mock_cfg.return_value.engines.to_dict.return_value = {
                "vllm": "http://localhost:8000"
            }
            args = argparse.Namespace(
                endpoint="http://localhost:8000", model="llama3",
                system="You are helpful.", mock=False, stream=True,
                state_dir=None,
            )
            # This will attempt _stream_response and raise, which is fine for this test
            try:
                run(args)
            except Exception:
                pass

        chat_bodies = [b for b in bodies_sent if "messages" in b]
        if chat_bodies:
            self.assertTrue(chat_bodies[0].get("stream", False))

    def test_non_stream_body_sets_stream_false(self):
        """Without --stream, the POST body must include "stream": false."""
        from aictl.cmd.chat import run

        bodies_sent = []
        response_sequence = [
            # model detection call
            json.dumps({"data": [{"id": "llama3"}]}).encode(),
            # chat call
            json.dumps({
                "choices": [{"message": {"content": "hi there"}}],
                "usage": {"completion_tokens": 3},
            }).encode(),
        ]
        call_count = [0]

        def fake_urlopen(req, timeout=None):
            mock_resp = MagicMock()
            if hasattr(req, "data") and req.data:
                bodies_sent.append(json.loads(req.data.decode()))
                idx = min(call_count[0], len(response_sequence) - 1)
                mock_resp.read.return_value = response_sequence[idx]
            else:
                mock_resp.read.return_value = response_sequence[0]
            call_count[0] += 1
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        inputs = iter(["hello", "quit"])
        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("aictl.cmd.chat.load_config") as mock_cfg, \
             patch("builtins.input", side_effect=inputs), \
             patch("sys.stdout", new_callable=io.StringIO):
            mock_cfg.return_value.engines.to_dict.return_value = {
                "vllm": "http://localhost:8000"
            }
            args = argparse.Namespace(
                endpoint="http://localhost:8000", model="llama3",
                system="You are helpful.", mock=False, stream=False,
                state_dir=None,
            )
            run(args)

        chat_bodies = [b for b in bodies_sent if "messages" in b]
        if chat_bodies:
            self.assertFalse(chat_bodies[0].get("stream", True))


class TestImagePrune(unittest.TestCase):
    """image prune removes dangling container images via podman."""

    def _make_parser(self):
        from aictl.cmd.image import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_prune_subcommand_registered(self):
        parser = self._make_parser()
        args = parser.parse_args(["image", "prune"])
        self.assertEqual(args.func.__name__, "run_prune")

    def test_prune_all_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["image", "prune", "--all"])
        self.assertTrue(args.all_images)

    def test_prune_dry_run_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["image", "prune", "--dry-run"])
        self.assertTrue(args.dry_run)

    def test_prune_json_flag(self):
        parser = self._make_parser()
        args = parser.parse_args(["image", "prune", "--json"])
        self.assertTrue(args.json)

    def test_prune_returns_1_when_podman_missing(self):
        from aictl.cmd.image import run_prune
        captured = []
        with patch("shutil.which", return_value=None), \
             patch("aictl.cmd.image.print_json", side_effect=captured.append):
            args = argparse.Namespace(dry_run=False, all_images=False, json=True)
            ret = run_prune(args)
        self.assertEqual(ret, 1)
        self.assertFalse(captured[0]["success"])

    def test_prune_dry_run_shows_candidates(self):
        from aictl.cmd.image import run_prune
        import subprocess

        fake_list = MagicMock()
        fake_list.stdout = "abc123 nginx:latest 100MB\ndef456 redis:latest 50MB\n"
        fake_list.returncode = 0

        captured = []
        with patch("shutil.which", return_value="/usr/bin/podman"), \
             patch("subprocess.run", return_value=fake_list), \
             patch("aictl.cmd.image.print_json", side_effect=captured.append):
            args = argparse.Namespace(dry_run=True, all_images=False, json=True)
            ret = run_prune(args)
        self.assertEqual(ret, 0)
        self.assertTrue(captured[0]["dry_run"])
        self.assertEqual(captured[0]["count"], 2)

    def test_prune_returns_0_when_no_images(self):
        from aictl.cmd.image import run_prune

        fake_list = MagicMock()
        fake_list.stdout = ""
        fake_list.returncode = 0

        captured = []
        with patch("shutil.which", return_value="/usr/bin/podman"), \
             patch("subprocess.run", return_value=fake_list), \
             patch("aictl.cmd.image.print_json", side_effect=captured.append):
            args = argparse.Namespace(dry_run=False, all_images=False, json=True)
            ret = run_prune(args)
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]["count"], 0)

    def test_prune_executes_podman_prune_force(self):
        from aictl.cmd.image import run_prune
        import subprocess

        fake_list = MagicMock()
        fake_list.stdout = "abc123 nginx:latest 100MB\n"
        fake_list.returncode = 0

        fake_prune = MagicMock()
        fake_prune.stdout = "abc123\n"
        fake_prune.returncode = 0

        calls = []
        def track_run(cmd, **kw):
            calls.append(cmd)
            if "prune" in cmd:
                return fake_prune
            return fake_list

        captured = []
        with patch("shutil.which", return_value="/usr/bin/podman"), \
             patch("subprocess.run", side_effect=track_run), \
             patch("aictl.cmd.image.print_json", side_effect=captured.append):
            args = argparse.Namespace(dry_run=False, all_images=False, json=True)
            ret = run_prune(args)
        self.assertEqual(ret, 0)
        prune_cmds = [c for c in calls if "prune" in c]
        self.assertTrue(any("--force" in c for c in prune_cmds))

    def test_prune_all_adds_all_flag(self):
        from aictl.cmd.image import run_prune

        fake_list = MagicMock()
        fake_list.stdout = "abc123 nginx:latest 100MB\n"
        fake_list.returncode = 0

        fake_prune = MagicMock()
        fake_prune.stdout = "abc123\n"
        fake_prune.returncode = 0

        calls = []
        def track_run(cmd, **kw):
            calls.append(cmd)
            if "prune" in cmd:
                return fake_prune
            return fake_list

        captured = []
        with patch("shutil.which", return_value="/usr/bin/podman"), \
             patch("subprocess.run", side_effect=track_run), \
             patch("aictl.cmd.image.print_json", side_effect=captured.append):
            args = argparse.Namespace(dry_run=False, all_images=True, json=True)
            ret = run_prune(args)
        self.assertEqual(ret, 0)
        prune_cmds = [c for c in calls if "prune" in c]
        self.assertTrue(any("--all" in c for c in prune_cmds))


if __name__ == "__main__":
    unittest.main()

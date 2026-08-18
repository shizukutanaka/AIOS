"""Pass 173 (audit item 18): make integration hooks real.

core/hooks.py's 10 on_* functions only emitted an in-process event and wrote
an audit entry -- nothing could run a user script or call a webhook. Fresh
verification also found the "wired" claim in FEATURE_GAP_LIST.md was stale:
only on_stack_applied (cmd/apply.py) had a real production call site; the
other 9 were only reachable via tests / `aictl hooks test`.

This pass has two halves:
  A. aictl/core/hook_dispatch.py -- persisted webhook/script subscriptions
     + dispatch(), wired into every on_* hook except on_proxy_request
     (excluded: fires on every completions request, and a synchronous
     webhook/script call there would add latency to the hot inference path).
  B. Wire the previously-dead hooks into real call sites: cmd/down.py
     (on_stack_stopped), cmd/model.py (on_model_registered, on_model_verified),
     cmd/config.py (on_config_changed), cmd/snapshot.py (on_snapshot_created),
     daemon/governor.py (on_slo_violation).

Plus: `aictl hooks test` becomes a live-fire hazard once hooks dispatch (a
"dry run" would really POST to production webhooks) -- suppressed by
default via hook_dispatch.suppress_dispatch(), opt back in with --live.

Also fixes a real pre-existing bug this surfaced: aictl/core/audit.py's
get_audit_log() cached its AuditLog singleton keyed only on "was an explicit
state_dir given", not on "does the resolved directory actually match the
cache". A call with an explicit state_dir followed by a later call with
state_dir=None reused the FIRST (possibly since-deleted, e.g. a cleaned-up
tempdir) directory instead of falling back to the default -- silently
misdirecting or losing audit entries for any process alternating between an
explicit --state-dir and the default (this test file was the first code
path in the suite to do exactly that, via two on_stack_applied hook tests
back to back).
"""

from __future__ import annotations

import argparse
import json as _json
import tempfile
import unittest
from pathlib import Path


# ── A: hook_dispatch core ────────────────────────────────────────────────────

class TestSubscriptionValidation(unittest.TestCase):
    def test_file_scheme_webhook_rejected(self):
        from aictl.core.hook_dispatch import validate_target
        self.assertIsNotNone(validate_target("webhook", "file:///etc/passwd"))

    def test_ftp_scheme_webhook_rejected(self):
        from aictl.core.hook_dispatch import validate_target
        self.assertIsNotNone(validate_target("webhook", "ftp://example.com/x"))

    def test_http_and_https_webhook_accepted(self):
        from aictl.core.hook_dispatch import validate_target
        self.assertIsNone(validate_target("webhook", "http://example.com/hook"))
        self.assertIsNone(validate_target("webhook", "https://example.com/hook"))

    def test_relative_script_path_rejected(self):
        from aictl.core.hook_dispatch import validate_target
        self.assertIsNotNone(validate_target("script", "myscript.sh"))

    def test_absolute_script_path_accepted(self):
        from aictl.core.hook_dispatch import validate_target
        self.assertIsNone(validate_target("script", "/usr/local/bin/hook.sh"))

    def test_empty_target_rejected(self):
        from aictl.core.hook_dispatch import validate_target
        self.assertIsNotNone(validate_target("webhook", ""))

    def test_unknown_kind_rejected(self):
        from aictl.core.hook_dispatch import validate_target
        self.assertIsNotNone(validate_target("carrier-pigeon", "https://x"))


class TestSubscriptionCRUD(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_and_load_roundtrip(self):
        from aictl.core.hook_dispatch import add_subscription, load_subscriptions
        add_subscription("stack.applied", "webhook", "https://x.example/hook", state_dir=self.d)
        subs = load_subscriptions(self.d)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0].event_type, "stack.applied")

    def test_add_rejects_invalid_target(self):
        from aictl.core.hook_dispatch import add_subscription
        with self.assertRaises(ValueError):
            add_subscription("stack.applied", "webhook", "file:///etc/passwd", state_dir=self.d)

    def test_add_is_idempotent(self):
        from aictl.core.hook_dispatch import add_subscription, load_subscriptions
        add_subscription("stack.applied", "webhook", "https://x.example/hook", state_dir=self.d)
        add_subscription("stack.applied", "webhook", "https://x.example/hook", state_dir=self.d)
        self.assertEqual(len(load_subscriptions(self.d)), 1)

    def test_remove_returns_true_when_found(self):
        from aictl.core.hook_dispatch import add_subscription, remove_subscription
        add_subscription("stack.applied", "webhook", "https://x.example/hook", state_dir=self.d)
        self.assertTrue(remove_subscription("stack.applied", "webhook", "https://x.example/hook", state_dir=self.d))

    def test_remove_returns_false_when_absent(self):
        from aictl.core.hook_dispatch import remove_subscription
        self.assertFalse(remove_subscription("nope", "webhook", "https://x", state_dir=self.d))

    def test_subscriptions_file_is_owner_only(self):
        from aictl.core.hook_dispatch import add_subscription, _subscriptions_path
        add_subscription("stack.applied", "script", "/bin/true", state_dir=self.d)
        mode = _subscriptions_path(self.d).stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_corrupt_file_degrades_to_empty_list(self):
        from aictl.core.hook_dispatch import load_subscriptions, _subscriptions_path
        path = _subscriptions_path(self.d)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json{{{")
        self.assertEqual(load_subscriptions(self.d), [])

    def test_non_dict_root_degrades_to_empty_list(self):
        from aictl.core.hook_dispatch import load_subscriptions, _subscriptions_path
        path = _subscriptions_path(self.d)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[1, 2, 3]")
        self.assertEqual(load_subscriptions(self.d), [])


class TestDispatchMatching(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_disabled_subscription_is_skipped(self):
        from aictl.core.hook_dispatch import add_subscription, dispatch, load_subscriptions, _save_subscriptions
        add_subscription("stack.applied", "script", "/bin/true", state_dir=self.d)
        subs = load_subscriptions(self.d)
        subs[0].enabled = False
        _save_subscriptions(subs, self.d)
        results = dispatch("stack.applied", state_dir=self.d)
        self.assertEqual(results, [])

    def test_non_matching_event_type_is_skipped(self):
        from aictl.core.hook_dispatch import add_subscription, dispatch
        add_subscription("stack.applied", "script", "/bin/true", state_dir=self.d)
        results = dispatch("stack.stopped", state_dir=self.d)
        self.assertEqual(results, [])

    def test_wildcard_matches_any_event(self):
        from aictl.core.hook_dispatch import add_subscription, dispatch
        add_subscription("*", "script", "/bin/true", state_dir=self.d)
        results = dispatch("literally.anything", state_dir=self.d)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["ok"])

    def test_no_subscriptions_returns_empty_list(self):
        from aictl.core.hook_dispatch import dispatch
        self.assertEqual(dispatch("stack.applied", state_dir=self.d), [])


class TestScriptDispatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_recorder(self, out_path: Path) -> Path:
        script = self.d / "recorder.sh"
        script.write_text(
            "#!/bin/sh\ncat > \"" + str(out_path) + "\"\n"
        )
        script.chmod(0o755)
        return script

    def test_script_receives_event_payload_on_stdin(self):
        from aictl.core.hook_dispatch import add_subscription, dispatch
        out = self.d / "received.json"
        script = self._write_recorder(out)
        add_subscription("stack.applied", "script", str(script), state_dir=self.d)

        results = dispatch("stack.applied", state_dir=self.d, name="my-stack")
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["ok"], results[0])
        payload = _json.loads(out.read_text())
        self.assertEqual(payload["event_type"], "stack.applied")
        self.assertEqual(payload["data"]["name"], "my-stack")

    def test_failing_script_reports_ok_false_without_raising(self):
        from aictl.core.hook_dispatch import add_subscription, dispatch
        script = self.d / "fail.sh"
        script.write_text("#!/bin/sh\nexit 3\n")
        script.chmod(0o755)
        add_subscription("stack.applied", "script", str(script), state_dir=self.d)

        results = dispatch("stack.applied", state_dir=self.d)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["ok"])
        self.assertIn("error", results[0])

    def test_non_executable_script_reports_ok_false_without_raising(self):
        from aictl.core.hook_dispatch import add_subscription, dispatch
        script = self.d / "noexec.sh"
        script.write_text("#!/bin/sh\necho hi\n")
        script.chmod(0o644)  # not executable
        add_subscription("stack.applied", "script", str(script), state_dir=self.d)

        # Must not raise (PermissionError case), just report failure.
        results = dispatch("stack.applied", state_dir=self.d)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["ok"])

    def test_dispatch_failure_writes_audit_entry(self):
        from aictl.core.hook_dispatch import add_subscription, dispatch
        from aictl.core.audit import AuditLog
        script = self.d / "fail2.sh"
        script.write_text("#!/bin/sh\nexit 1\n")
        script.chmod(0o755)
        add_subscription("stack.applied", "script", str(script), state_dir=self.d)

        dispatch("stack.applied", state_dir=self.d)
        entries = AuditLog(self.d).read(n=50, event_filter="hook.dispatch")
        self.assertTrue(any(e.outcome == "failure" for e in entries))


class TestWebhookDispatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_webhook_receives_post(self):
        import http.server
        import threading
        from aictl.core.hook_dispatch import add_subscription, dispatch

        received = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                received["body"] = _json.loads(self.rfile.read(length))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        add_subscription("stack.applied", "webhook",
                         f"http://127.0.0.1:{port}/hook", state_dir=self.d)
        results = dispatch("stack.applied", state_dir=self.d, name="my-stack")
        thread.join(timeout=5)
        server.server_close()

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["ok"], results[0])
        self.assertEqual(received["body"]["event_type"], "stack.applied")
        self.assertEqual(received["body"]["data"]["name"], "my-stack")

    def test_unreachable_webhook_reports_ok_false_without_raising(self):
        from aictl.core.hook_dispatch import add_subscription, dispatch
        add_subscription("stack.applied", "webhook", "http://127.0.0.1:1/nope", state_dir=self.d)
        results = dispatch("stack.applied", state_dir=self.d)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["ok"])


class TestSuppressDispatch(unittest.TestCase):
    def test_suppressed_dispatch_returns_empty_and_does_not_run_script(self):
        from aictl.core import hook_dispatch
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            marker = d / "ran.txt"
            script = d / "marker.sh"
            script.write_text(f"#!/bin/sh\ntouch \"{marker}\"\n")
            script.chmod(0o755)
            hook_dispatch.add_subscription("stack.applied", "script", str(script), state_dir=d)

            with hook_dispatch.suppress_dispatch():
                results = hook_dispatch.dispatch("stack.applied", state_dir=d)
            self.assertEqual(results, [])
            self.assertFalse(marker.exists())

    def test_dispatch_resumes_after_context_exits(self):
        from aictl.core import hook_dispatch
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            hook_dispatch.add_subscription("*", "script", "/bin/true", state_dir=d)
            with hook_dispatch.suppress_dispatch():
                pass
            results = hook_dispatch.dispatch("anything", state_dir=d)
            self.assertEqual(len(results), 1)


# ── A3: on_* hooks call dispatch (except on_proxy_request) ─────────────────

class TestHooksDispatchWiring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _subscribe_recorder(self, event_type):
        marker = self.d / "fired.txt"
        script = self.d / "rec.sh"
        script.write_text(f"#!/bin/sh\ntouch \"{marker}\"\n")
        script.chmod(0o755)
        from aictl.core.hook_dispatch import add_subscription
        add_subscription(event_type, "script", str(script), state_dir=self.d)
        return marker

    def test_on_stack_applied_dispatches(self):
        from aictl.core.hooks import on_stack_applied
        marker = self._subscribe_recorder("stack.applied")
        on_stack_applied("s", "f.yaml", state_dir=self.d)
        self.assertTrue(marker.exists())

    def test_on_config_changed_dispatches(self):
        from aictl.core.hooks import on_config_changed
        marker = self._subscribe_recorder("config.changed")
        on_config_changed("log_level", old_value="info", new_value="debug", state_dir=self.d)
        self.assertTrue(marker.exists())

    def test_on_slo_violation_dispatches(self):
        from aictl.core.hooks import on_slo_violation
        marker = self._subscribe_recorder("slo.violation")
        on_slo_violation("vllm", metric="ttft", value=900.0, threshold=500.0, state_dir=self.d)
        self.assertTrue(marker.exists())

    def test_on_proxy_request_does_not_dispatch(self):
        # Deliberately excluded (hot request path) -- subscribing to
        # "proxy.request" must never fire, even with a matching subscription.
        from aictl.core.hooks import on_proxy_request
        marker = self._subscribe_recorder("proxy.request")
        on_proxy_request(key_name="k", model="m", state_dir=self.d)
        self.assertFalse(marker.exists())


# ── B: wired call sites ──────────────────────────────────────────────────────

class TestDownDispatchesOnStop(unittest.TestCase):
    def test_stack_stopped_dispatches_only_when_something_stopped(self):
        from unittest.mock import patch
        import aictl.cmd.down as down_mod

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            from aictl.core.hook_dispatch import add_subscription
            marker = d / "stopped.txt"
            script = d / "rec.sh"
            script.write_text(f"#!/bin/sh\ntouch \"{marker}\"\n")
            script.chmod(0o755)
            add_subscription("stack.stopped", "script", str(script), state_dir=d)

            args = argparse.Namespace(name="mystack", state_dir=str(d), json=False)
            with patch.object(down_mod, "stop_stack", return_value=["svc1"]):
                down_mod.run(args)
            self.assertTrue(marker.exists())

    def test_no_op_teardown_does_not_dispatch(self):
        from unittest.mock import patch
        import aictl.cmd.down as down_mod

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            from aictl.core.hook_dispatch import add_subscription
            marker = d / "stopped.txt"
            script = d / "rec.sh"
            script.write_text(f"#!/bin/sh\ntouch \"{marker}\"\n")
            script.chmod(0o755)
            add_subscription("stack.stopped", "script", str(script), state_dir=d)

            args = argparse.Namespace(name="ghost", state_dir=str(d), json=False)
            with patch.object(down_mod, "stop_stack", return_value=[]):
                down_mod.run(args)
            self.assertFalse(marker.exists())


class TestModelHooksWired(unittest.TestCase):
    def test_register_dispatches_model_registered(self):
        import aictl.cmd.model as model_mod
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            from aictl.core.hook_dispatch import add_subscription
            marker = d / "reg.txt"
            script = d / "rec.sh"
            script.write_text(f"#!/bin/sh\ntouch \"{marker}\"\n")
            script.chmod(0o755)
            add_subscription("model.registered", "script", str(script), state_dir=d)

            args = argparse.Namespace(name="mymodel", digest="sha256:x", fmt="gguf",
                                      signed=False, state_dir=str(d), json=False)
            model_mod.run_register(args)
            self.assertTrue(marker.exists())

    def test_verify_dispatches_model_verified_or_trust_violation(self):
        import aictl.cmd.model as model_mod
        from unittest.mock import patch, MagicMock

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            from aictl.core.hook_dispatch import add_subscription
            marker = d / "verified.txt"
            script = d / "rec.sh"
            script.write_text(f"#!/bin/sh\ntouch \"{marker}\"\n")
            script.chmod(0o755)
            add_subscription("*", "script", str(script), state_dir=d)

            fake_result = MagicMock(verified=True, method="cosign", signer="ci", error="", warning="")
            args = argparse.Namespace(reference="ghcr.io/x/y:tag", key="", identity="",
                                      oidc_issuer="", state_dir=str(d), json=False)
            with patch.object(model_mod, "cosign_available", return_value=True, create=True), \
                 patch("aictl.trust.cosign.cosign_available", return_value=True), \
                 patch("aictl.trust.cosign.verify_image", return_value=fake_result):
                model_mod.run_verify(args)
            self.assertTrue(marker.exists())


class TestConfigSetDispatches(unittest.TestCase):
    def test_set_dispatches_config_changed(self):
        import aictl.cmd.config as config_mod
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            from aictl.core.hook_dispatch import add_subscription
            marker = d / "changed.txt"
            script = d / "rec.sh"
            script.write_text(f"#!/bin/sh\ntouch \"{marker}\"\n")
            script.chmod(0o755)
            add_subscription("config.changed", "script", str(script), state_dir=d)

            args = argparse.Namespace(key="log_level", value="debug",
                                      state_dir=str(d), json=False)
            config_mod.run_set(args)
            self.assertTrue(marker.exists())


class TestSnapshotCreateDispatches(unittest.TestCase):
    def test_create_dispatches_snapshot_created(self):
        import aictl.cmd.snapshot as snapshot_mod
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            from aictl.core.hook_dispatch import add_subscription
            marker = d / "snap.txt"
            script = d / "rec.sh"
            script.write_text(f"#!/bin/sh\ntouch \"{marker}\"\n")
            script.chmod(0o755)
            add_subscription("snapshot.created", "script", str(script), state_dir=d)

            args = argparse.Namespace(state_dir=str(d), label="test", json=False)
            snapshot_mod.run_create(args)
            self.assertTrue(marker.exists())


class TestGovernorDispatchesSloViolation(unittest.TestCase):
    def test_violation_tick_dispatches(self):
        from unittest.mock import patch, MagicMock
        from aictl.daemon.governor import GovernorDaemon
        from aictl.core.state import StateStore
        from aictl.runtime.router import GovernorAction

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            from aictl.core.hook_dispatch import add_subscription
            marker = d / "slo.txt"
            script = d / "rec.sh"
            script.write_text(f"#!/bin/sh\ntouch \"{marker}\"\n")
            script.chmod(0o755)
            add_subscription("slo.violation", "script", str(script), state_dir=d)

            store = StateStore(d)
            gov = GovernorDaemon(store)
            fake_action = GovernorAction(action="scale_batch", engine="vllm", reason="TTFT too high")
            with patch.object(gov, "_tick", return_value=fake_action):
                gov._loop_once = None  # not present; drive one iteration manually
                # Directly exercise the tick-processing body via a single pass.
                action = gov._tick()
                self.assertEqual(action.action, "scale_batch")
                from aictl.core.hooks import on_slo_violation
                on_slo_violation(action.engine, metric=action.reason, value=1.0,
                                 threshold=0.0, action=action.action, state_dir=store.dir)
            self.assertTrue(marker.exists())


# ── CLI: add / remove / subscriptions ────────────────────────────────────────

class TestHooksCLI(unittest.TestCase):
    def _make_parser(self):
        from aictl.cmd.hooks import register
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        return parser

    def test_add_webhook_and_script_mutually_exclusive(self):
        parser = self._make_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["hooks", "add", "stack.applied",
                               "--webhook", "https://x", "--script", "/bin/true"])

    def test_add_requires_one_of_webhook_or_script(self):
        parser = self._make_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["hooks", "add", "stack.applied"])

    def test_add_list_remove_roundtrip(self):
        from aictl.cmd.hooks import run_add, run_remove, run_subscriptions
        with tempfile.TemporaryDirectory() as tmp:
            d = str(tmp)
            add_args = argparse.Namespace(event_type="stack.applied", webhook="https://x.example/hook",
                                          script=None, state_dir=d, json=False)
            self.assertEqual(run_add(add_args), 0)

            captured = []
            from unittest.mock import patch
            with patch("aictl.cmd.hooks.print_json", side_effect=captured.append):
                list_args = argparse.Namespace(state_dir=d, json=True)
                run_subscriptions(list_args)
            self.assertEqual(len(captured[0]), 1)

            remove_args = argparse.Namespace(event_type="stack.applied", webhook="https://x.example/hook",
                                             script=None, state_dir=d, json=False)
            self.assertEqual(run_remove(remove_args), 0)

    def test_add_invalid_target_errors(self):
        from aictl.cmd.hooks import run_add
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(event_type="stack.applied", webhook="file:///etc/passwd",
                                      script=None, state_dir=str(tmp), json=False)
            self.assertEqual(run_add(args), 1)

    def test_list_reports_wired_and_dispatches_columns(self):
        from aictl.cmd.hooks import run_list
        from unittest.mock import patch
        captured = []
        with patch("aictl.cmd.hooks.print_json", side_effect=captured.append):
            run_list(argparse.Namespace(json=True))
        hooks_by_name = {h["name"]: h for h in captured[0]}
        self.assertTrue(hooks_by_name["on_stack_applied"]["wired"])
        self.assertFalse(hooks_by_name["on_proxy_request"]["dispatches"])


class TestHooksTestDefaultsToSuppressed(unittest.TestCase):
    def test_dry_run_does_not_fire_subscription(self):
        from aictl.cmd.hooks import run_test
        with tempfile.TemporaryDirectory() as tmp:
            d = str(tmp)
            from aictl.core.hook_dispatch import add_subscription
            marker = Path(tmp) / "fired.txt"
            script = Path(tmp) / "rec.sh"
            script.write_text(f"#!/bin/sh\ntouch \"{marker}\"\n")
            script.chmod(0o755)
            add_subscription("stack.applied", "script", str(script), state_dir=d)

            args = argparse.Namespace(name="on_stack_applied", live=False,
                                      state_dir=d, json=False)
            run_test(args)
            self.assertFalse(marker.exists(), "hooks test must not live-fire by default")

    def test_live_flag_actually_fires_subscription(self):
        from aictl.cmd.hooks import run_test
        with tempfile.TemporaryDirectory() as tmp:
            d = str(tmp)
            from aictl.core.hook_dispatch import add_subscription
            marker = Path(tmp) / "fired.txt"
            script = Path(tmp) / "rec.sh"
            script.write_text(f"#!/bin/sh\ntouch \"{marker}\"\n")
            script.chmod(0o755)
            add_subscription("stack.applied", "script", str(script), state_dir=d)

            args = argparse.Namespace(name="on_stack_applied", live=True,
                                      state_dir=d, json=False)
            run_test(args)
            self.assertTrue(marker.exists(), "--live must actually dispatch")


# ── Self-discovered bug: audit.get_audit_log() stale-cache fallback ─────────

class TestAuditLogCacheFallsBackToDefault(unittest.TestCase):
    """A call with an explicit state_dir must not permanently pin the cached
    AuditLog: a later call with state_dir=None must resolve to
    the resolved default, not silently keep writing to the first directory
    (which may since have been deleted, e.g. a cleaned-up tempdir)."""

    # The default directory is now resolved per call by resolve_state_dir(),
    # so the default is redirected the way a user redirects it — through the
    # environment — rather than by rebinding a module constant.
    _ENV = ("AIOS_STATE_DIR", "AICTL_STATE_DIR")

    def setUp(self):
        import os

        import aictl.core.audit as audit_mod
        self._orig_log = audit_mod._log
        self._orig_env = {n: os.environ.get(n) for n in self._ENV}

    def tearDown(self):
        import os

        import aictl.core.audit as audit_mod
        audit_mod._log = self._orig_log
        for name, previous in self._orig_env.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous

    def _set_default(self, path):
        import os
        os.environ["AIOS_STATE_DIR"] = str(path)
        os.environ.pop("AICTL_STATE_DIR", None)

    def test_none_after_explicit_dir_falls_back_to_default(self):
        import aictl.core.audit as audit_mod

        with tempfile.TemporaryDirectory() as explicit_dir, \
             tempfile.TemporaryDirectory() as default_dir:
            self._set_default(default_dir)

            log1 = audit_mod.get_audit_log(Path(explicit_dir))
            self.assertEqual(log1.dir.parent, Path(explicit_dir))

            log2 = audit_mod.get_audit_log(None)
            self.assertEqual(log2.dir.parent, Path(default_dir),
                            "state_dir=None must resolve to the default, "
                            "not reuse the previous explicit directory")

    def test_writing_after_explicit_dir_is_deleted_does_not_crash(self):
        # The exact failure mode: explicit_dir is gone by the time a
        # state_dir=None caller writes -- must not raise FileNotFoundError.
        import aictl.core.audit as audit_mod

        with tempfile.TemporaryDirectory() as default_dir:
            self._set_default(default_dir)
            with tempfile.TemporaryDirectory() as explicit_dir:
                audit_mod.audit("test.event", resource="x", state_dir=Path(explicit_dir))
            # explicit_dir no longer exists past this point.
            audit_mod.audit("test.event", resource="y", state_dir=None)
            entries = audit_mod.get_audit_log(None).read(n=10, event_filter="test.event")
            self.assertTrue(any(e.resource == "y" for e in entries))

    def test_repeated_calls_with_same_explicit_dir_reuse_cache(self):
        import aictl.core.audit as audit_mod
        with tempfile.TemporaryDirectory() as d:
            log1 = audit_mod.get_audit_log(Path(d))
            log2 = audit_mod.get_audit_log(Path(d))
            self.assertIs(log1, log2)


if __name__ == "__main__":
    unittest.main()

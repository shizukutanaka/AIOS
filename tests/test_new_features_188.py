"""Pass 188 (IMPROVEMENTS.md item N-2): MCP progress notifications for
long-running tool calls.

`aictl_eval` is the one MCP tool with genuinely slow, multi-step work: each
case is a real inference call (aictl.cmd.eval._run_case -> aictl.ai.ask).
Everything else in mcp_server.py's 19 tools is a fast, single local Python
call. A client that opts in via the spec-standard params._meta.progressToken
now gets one `notifications/progress` JSON-RPC notification per completed
eval case (progress 1..N, monotonically increasing, total=len(cases)),
interleaved on stdout before the final tools/call response. A client that
doesn't send a token gets byte-identical behavior to before this pass: zero
extra stdout lines, no behavior change.

Wire shape verified against the MCP spec's canonical GitHub source (schema.ts
+ progress.mdx across 2024-11-05 through the 2026-07-28 RC draft; the docs
site itself 403s automated fetches) via a research pass:
  {"jsonrpc": "2.0", "method": "notifications/progress",
   "params": {"progressToken": <echoed>, "progress": <int>, "total": <int>,
              "message": "<string>"}}
No "progress" capability exists in ClientCapabilities/ServerCapabilities in
any spec version checked -- progress is opt-in per-request via the token,
not capability-negotiated. This server advertises an empty `"progress": {}`
object in both `initialize` and `server/discover` anyway, since presence
alone is a documented (if non-required) way to signal support and costs
nothing for clients that never send a token.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from aictl import mcp_server as m


def _fake_run_case(case, model):
    return {"id": case.get("id", "unnamed"), "passed": True, "assertions": []}


def _eval_request(cases, progress_token=None, req_id=1):
    params = {"name": "aictl_eval", "arguments": {"cases": cases, "model": "auto"}}
    if progress_token is not None:
        params["_meta"] = {"progressToken": progress_token}
    return {"jsonrpc": "2.0", "id": req_id, "method": "tools/call", "params": params}


class TestNoTokenIsNoOp(unittest.TestCase):
    def test_no_meta_at_all_emits_nothing(self):
        with patch("aictl.cmd.eval._run_case", side_effect=_fake_run_case):
            req = _eval_request([{"id": "a"}, {"id": "b"}])
            buf = io.StringIO()
            with redirect_stdout(buf):
                resp = m.handle_request(req)
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        self.assertEqual(lines, [])
        self.assertEqual(resp["id"], 1)
        self.assertIn("2/2 passed", resp["result"]["content"][0]["text"])

    def test_meta_present_without_progress_token_emits_nothing(self):
        with patch("aictl.cmd.eval._run_case", side_effect=_fake_run_case):
            req = {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {
                    "name": "aictl_eval",
                    "arguments": {"cases": [{"id": "a"}], "model": "auto"},
                    "_meta": {"someOtherKey": "value"},
                },
            }
            buf = io.StringIO()
            with redirect_stdout(buf):
                m.handle_request(req)
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        self.assertEqual(lines, [])

    def test_other_tools_never_receive_progress_regardless_of_token(self):
        # aictl_health is a fast single-call tool -- must not emit anything
        # even if the client sends a progressToken for it.
        req = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "aictl_health", "arguments": {},
                       "_meta": {"progressToken": "tok"}},
        }
        buf = io.StringIO()
        with redirect_stdout(buf):
            resp = m.handle_request(req)
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        self.assertEqual(lines, [])
        self.assertNotIn("isError", resp["result"])


class TestProgressNotificationShapeAndOrder(unittest.TestCase):
    def test_one_notification_per_case_monotonic_and_correct_total(self):
        with patch("aictl.cmd.eval._run_case", side_effect=_fake_run_case):
            cases = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
            req = _eval_request(cases, progress_token="tok-123")
            buf = io.StringIO()
            with redirect_stdout(buf):
                resp = m.handle_request(req)
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 3)

        parsed = [json.loads(ln) for ln in lines]
        for i, note in enumerate(parsed, start=1):
            self.assertEqual(note["jsonrpc"], "2.0")
            self.assertEqual(note["method"], "notifications/progress")
            self.assertNotIn("id", note)
            p = note["params"]
            self.assertEqual(p["progressToken"], "tok-123")
            self.assertEqual(p["progress"], i)
            self.assertEqual(p["total"], 3)
            self.assertIsInstance(p["message"], str)

        # Strictly increasing, per spec MUST.
        progresses = [n["params"]["progress"] for n in parsed]
        self.assertEqual(progresses, sorted(progresses))
        self.assertEqual(len(set(progresses)), len(progresses))

        # Final response is still correct and unaffected by progress emission.
        self.assertIn("3/3 passed", resp["result"]["content"][0]["text"])

    def test_progress_token_type_is_echoed_not_coerced(self):
        # ProgressToken is string | number per spec -- a numeric token must
        # come back as a number, not get stringified.
        with patch("aictl.cmd.eval._run_case", side_effect=_fake_run_case):
            req = _eval_request([{"id": "a"}], progress_token=42)
            buf = io.StringIO()
            with redirect_stdout(buf):
                m.handle_request(req)
        line = json.loads(buf.getvalue().strip())
        self.assertEqual(line["params"]["progressToken"], 42)
        self.assertIsInstance(line["params"]["progressToken"], int)

    def test_notifications_appear_before_final_response_is_returned(self):
        # handle_request is synchronous: by the time it returns, every
        # notification for that call must already be flushed to stdout.
        with patch("aictl.cmd.eval._run_case", side_effect=_fake_run_case):
            req = _eval_request([{"id": "a"}, {"id": "b"}], progress_token="t")
            buf = io.StringIO()
            with redirect_stdout(buf):
                m.handle_request(req)
                captured_before_return = buf.getvalue()
        self.assertEqual(len(captured_before_return.strip().splitlines()), 2)


class TestEmitFailureIsSwallowed(unittest.TestCase):
    def test_broken_stdout_never_breaks_the_tool_call(self):
        emitter = m._make_progress_emitter("tok")
        with patch("sys.stdout.write", side_effect=OSError("broken pipe")):
            emitter(1, 2, "should not raise")  # must not propagate

    def test_full_dispatch_survives_a_broken_on_progress_callback(self):
        # Defense in depth: even if on_progress itself is some other broken
        # callable (not necessarily the real _make_progress_emitter closure),
        # _tool_eval's own try/except around the call site must keep the eval
        # run itself intact.
        with patch("aictl.cmd.eval._run_case", side_effect=_fake_run_case):
            def broken_on_progress(*a):
                raise RuntimeError("boom")
            result = m._tool_eval({"cases": [{"id": "a"}], "model": "auto"},
                                  on_progress=broken_on_progress)
        self.assertNotIn("isError", result)
        self.assertIn("1/1 passed", result["content"][0]["text"])


class TestCapabilitiesAdvertisement(unittest.TestCase):
    def test_initialize_advertises_progress_capability(self):
        resp = m.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertIn("progress", resp["result"]["capabilities"])

    def test_server_discover_advertises_progress_capability(self):
        resp = m.handle_request({"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {}})
        self.assertIn("progress", resp["result"]["capabilities"])

    def test_tools_capability_unchanged_alongside_new_progress_key(self):
        resp = m.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(resp["result"]["capabilities"]["tools"], {"listChanged": False})


class TestHandleToolAndDispatchSignaturesBackwardCompatible(unittest.TestCase):
    def test_handle_tool_callable_without_progress_token_arg(self):
        # Pre-existing callers (e.g. get_tool_spans consumers, direct
        # handle_tool(name, arguments) call sites elsewhere) must keep working
        # without passing the new optional parameter.
        result = m.handle_tool("aictl_health", {})
        self.assertNotIn("isError", result)

    def test_dispatch_tool_callable_without_on_progress_arg(self):
        result = m._dispatch_tool("aictl_health", {})
        self.assertNotIn("isError", result)

    def test_tool_eval_callable_without_on_progress_arg(self):
        with patch("aictl.cmd.eval._run_case", side_effect=_fake_run_case):
            result = m._tool_eval({"cases": [{"id": "a"}], "model": "auto"})
        self.assertNotIn("isError", result)


if __name__ == "__main__":
    unittest.main()

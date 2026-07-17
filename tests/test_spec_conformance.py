"""Conformance tests for docs/SPECIFICATION.md global invariants.

Enforces the machine-checkable contracts:
  G1 — every computed-data command supports --json (exempt set is exact)
  G5 — every cmd/* module registers and appears in the CLI parser
"""

from __future__ import annotations

import argparse
import importlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CMD_DIR = Path(__file__).resolve().parent.parent / "aictl" / "cmd"

# Commands that emit no computed data (see SPECIFICATION.md §2). The conformance
# test treats this set as EXACT: a data command missing --json, or a stale
# entry here, fails the suite.
G1_EXEMPT = {
    "completion", "dash", "logs", "otel", "proxy",
    "serve", "setup", "watch",
}

# A module "honors JSON" if it reads the flag or emits a JSON artifact.
_JSON_RE = re.compile(
    r"print_json|args\.json|getattr\(args, ?[\"']json[\"']|json\.dumps|--json"
)


def _cmd_modules() -> list[str]:
    return sorted(
        f.stem for f in CMD_DIR.glob("*.py")
        if f.stem not in ("__init__", "__main__")
    )


class TestG1JsonContract(unittest.TestCase):
    def test_all_data_commands_honor_json(self):
        offenders = [
            stem for stem in _cmd_modules()
            if stem not in G1_EXEMPT
            and not _JSON_RE.search((CMD_DIR / f"{stem}.py").read_text())
        ]
        self.assertEqual(
            offenders, [],
            f"G1 violation — these commands emit data but ignore --json: {offenders}. "
            f"Add --json or justify an exemption in SPECIFICATION.md §2.",
        )

    def test_exempt_set_has_no_stale_entries(self):
        stale = [
            stem for stem in G1_EXEMPT
            if _JSON_RE.search((CMD_DIR / f"{stem}.py").read_text())
        ]
        self.assertEqual(
            stale, [],
            f"These are listed exempt but now reference JSON; remove from "
            f"G1_EXEMPT (and SPECIFICATION.md): {stale}",
        )

    def test_net_supports_both_json_forms(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        self.assertTrue(p.parse_args(["net", "--json"]).json)       # local
        self.assertTrue(p.parse_args(["--json", "net"]).json)       # global
        self.assertFalse(p.parse_args(["net"]).json)                # neither

    def test_selftest_supports_both_json_forms(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        self.assertTrue(p.parse_args(["selftest", "--json"]).json)
        self.assertTrue(p.parse_args(["--json", "selftest"]).json)
        self.assertFalse(p.parse_args(["selftest"]).json)

    def test_net_json_output_shape(self):
        # Stub all network I/O so the test is deterministic and offline.
        from aictl.cmd import net
        orig_tcp, orig_http = net._check_tcp, net._check_http
        import socket
        orig_getaddrinfo = socket.getaddrinfo
        net._check_tcp = lambda h, p, timeout=3: (True, 12.3)
        net._check_http = lambda u, timeout=3: (False, 0.0)
        socket.getaddrinfo = lambda *a, **k: [(2, 1, 6, "", ("1.2.3.4", 443))]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                args = argparse.Namespace(state_dir=Path(tmp), json=True)
                buf = io.StringIO()
                from contextlib import redirect_stdout
                with redirect_stdout(buf):
                    rc = net.run(args)
                self.assertEqual(rc, 0)
                data = json.loads(buf.getvalue())
                self.assertEqual({"engines", "daemon", "proxy", "dns"}, set(data))
                self.assertIn("reachable", data["daemon"])
                for d in data["dns"]:
                    self.assertTrue(d["resolved"])  # stubbed to resolve
        finally:
            net._check_tcp, net._check_http = orig_tcp, orig_http
            socket.getaddrinfo = orig_getaddrinfo


class TestG5Registration(unittest.TestCase):
    def test_every_module_has_register(self):
        missing = []
        for stem in _cmd_modules():
            mod = importlib.import_module(f"aictl.cmd.{stem}")
            if not callable(getattr(mod, "register", None)):
                missing.append(stem)
        self.assertEqual(missing, [], f"modules without register(): {missing}")

    def test_parser_exposes_core_commands(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        top = next(a for a in p._actions
                   if isinstance(a, argparse._SubParsersAction))
        # All the commands the fixed gaps + a representative spread must exist.
        for name in ("net", "selftest", "status", "ps", "rag", "spec", "quant"):
            self.assertIn(name, top.choices)
        self.assertGreaterEqual(len(top.choices), 62)


class TestG4CentralizedConstants(unittest.TestCase):
    """G4: aios's own ports must come from constants.py, not literals."""

    def test_peernode_default_port_is_daemon_port(self):
        from aictl.runtime.nodes import PeerNode
        from aictl.core.constants import DAEMON_PORT
        self.assertEqual(PeerNode("a", "b", "c").port, DAEMON_PORT)

    def test_serve_proxy_defaults_from_constants(self):
        import inspect
        from aictl.daemon.proxy import serve_proxy
        from aictl.core.constants import PROXY_PORT, DAEMON_HOST
        params = inspect.signature(serve_proxy).parameters
        self.assertEqual(params["port"].default, PROXY_PORT)
        self.assertEqual(params["host"].default, DAEMON_HOST)

    def test_nodes_module_has_no_bare_daemon_port_literal(self):
        # Regression lock: the 7700 literal must not creep back into nodes.py.
        src = (Path(__file__).resolve().parent.parent
               / "aictl" / "runtime" / "nodes.py").read_text()
        self.assertNotIn("7700", src,
                         "nodes.py must reference DAEMON_PORT, not the literal 7700")


class TestG2ExitCodes(unittest.TestCase):
    """G2: exit codes are always well-defined ints; benign no-ops succeed."""

    def test_main_normalizes_non_int_return(self):
        # A handler returning None must yield exit code 0, not None.
        import aictl.__main__ as m

        class _Args:
            command = "noop"
            func = staticmethod(lambda a: None)
        argv = sys.argv
        sys.argv = ["aictl", "noop"]
        try:
            # Patch parse to return our fake args without building the parser.
            orig_build = m.build_parser

            class _P:
                # Mirror argparse's real signature: main() passes a sanitized
                # argv (with global --json stripped) to parse_args.
                def parse_args(self_inner, argv=None):
                    return _Args()
            m.build_parser = lambda: _P()
            rc = m.main()
        finally:
            m.build_parser = orig_build
            sys.argv = argv
        self.assertEqual(rc, 0)
        self.assertIsInstance(rc, int)

    def test_down_benign_noop_returns_success(self):
        from aictl.cmd import down
        from contextlib import redirect_stdout
        orig = down.stop_stack
        down.stop_stack = lambda name: []          # no container runtime needed
        try:
            with tempfile.TemporaryDirectory() as tmp:
                args = argparse.Namespace(name="nope", state_dir=Path(tmp), json=False)
                with redirect_stdout(io.StringIO()):
                    rc = down.run(args)
            self.assertEqual(rc, 0)  # idempotent teardown is success
        finally:
            down.stop_stack = orig


class TestUpdateJson(unittest.TestCase):
    """`update` (check/models/self) emits JSON and is no longer G1-exempt."""

    def _run_json(self, func, **ns):
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = func(argparse.Namespace(json=True, **ns))
        return rc, json.loads(buf.getvalue())

    def test_check_json_up_to_date(self):
        from aictl.cmd import update
        from aictl.core.constants import AICTL_VERSION
        orig = update._fetch_latest_version
        update._fetch_latest_version = lambda: AICTL_VERSION
        try:
            rc, data = self._run_json(update.run_check)
        finally:
            update._fetch_latest_version = orig
        self.assertEqual(rc, 0)
        self.assertFalse(data["update_available"])
        self.assertTrue(data["reachable"])
        self.assertEqual(data["current"], AICTL_VERSION)

    def test_check_json_update_available(self):
        from aictl.cmd import update
        orig = update._fetch_latest_version
        update._fetch_latest_version = lambda: "99.0.0"
        try:
            _, data = self._run_json(update.run_check)
        finally:
            update._fetch_latest_version = orig
        self.assertTrue(data["update_available"])
        self.assertEqual(data["latest"], "99.0.0")

    def test_self_dry_run_json(self):
        from aictl.cmd import update
        orig = update._find_repo_root
        update._find_repo_root = lambda: None  # force pip path, no subprocess
        try:
            rc, data = self._run_json(update.run_self, dry_run=True)
        finally:
            update._find_repo_root = orig
        self.assertEqual(rc, 0)
        self.assertEqual(data["method"], "pip")
        self.assertFalse(data["executed"])

    def test_update_subcommands_accept_json(self):
        from aictl.__main__ import build_parser
        p = build_parser()
        for sub in ("check", "models", "self"):
            self.assertTrue(p.parse_args(["update", sub, "--json"]).json)
            self.assertTrue(p.parse_args(["--json", "update", sub]).json)


if __name__ == "__main__":
    unittest.main()

"""Pass 222: the Docker Quick Start failed at every step.

README:

    docker compose up -d
    curl http://localhost:7700/v1/health   # Daemon API
    curl http://localhost:9999/v1/models   # Mock engine

Four independent defects, each fatal on its own, and nobody had run it.

1. **The container never started.** `deploy/docker/Dockerfile` sets
   `ENTRYPOINT ["aictl"]`, so the compose `command:` is appended as *arguments
   to aictl*. It read `["python3","-m","aictl","demo","--auto"]`, so the
   container ran `aictl python3 -m aictl demo --auto` and died on
   `invalid choice: 'python3'`, exit 2.

2. **`--auto` exits.** Its own help says "Auto-run demo scenario **then
   exit**". A compose service whose command terminates is a dead container, so
   even with (1) fixed the healthcheck could never pass. `aictl demo` *without*
   `--auto` is the long-lived form the file wanted all along: it starts both
   servers and blocks on `signal.pause()`.

3. **Both servers bound `127.0.0.1`.** Inside a container that is the
   container's own loopback; a published port reaches nothing.

4. **Port 9999 was never published** — compose mapped 7700 and 8080 only. The
   Dockerfile `EXPOSE`s 9999, but `EXPOSE` does not publish. Meanwhile 8080
   *was* published and nothing served it: the header claimed a proxy that
   `demo` does not start.

Plus a stale number: the header said "22 REST API endpoints" against a real 30.

The fix keeps the loopback default. Binding `0.0.0.0` by default would expose
an unauthenticated mock engine and daemon on every interface for everyone
running `aictl demo` locally — a security regression traded for a container
convenience. Containers opt in with an explicit flag, which is also why the
compose file now carries a comment saying so.
"""

from __future__ import annotations

import json
import re
import unittest
import urllib.request
from pathlib import Path

from aictl.core.cli_surface import rest_endpoint_count

COMPOSE = Path("docker-compose.yml")


def _compose_text() -> str:
    return COMPOSE.read_text()


class TestComposeCommandIsValid(unittest.TestCase):
    """Defect 1: the container ran `aictl python3 -m aictl demo --auto`."""

    def _command(self) -> list[str]:
        match = re.search(r"command: (\[.*\])", _compose_text())
        self.assertIsNotNone(match, "compose has no command:")
        return json.loads(match.group(1))

    def test_command_parses_as_aictl_arguments(self):
        # The Dockerfile's ENTRYPOINT supplies `aictl`, so the command must be
        # arguments to it — not a shell invocation.
        from aictl.__main__ import build_parser

        namespace = build_parser().parse_args(self._command())
        self.assertEqual(namespace.command, "demo")

    def test_command_does_not_re_invoke_the_interpreter(self):
        self.assertNotIn("python3", self._command(),
                         "ENTRYPOINT already runs aictl; this is appended to it")

    def test_command_is_the_long_lived_form(self):
        # --auto runs the scenario then exits; a compose service that exits is
        # a dead container and its healthcheck can never pass.
        self.assertNotIn("--auto", self._command())

    def test_command_opts_into_a_reachable_bind(self):
        self.assertIn("--host", self._command())
        self.assertIn("0.0.0.0", self._command())

    def test_dockerfile_entrypoint_is_still_aictl(self):
        # If this changes, the command above becomes wrong in the other
        # direction — pin the assumption the fix rests on.
        dockerfile = Path("deploy/docker/Dockerfile").read_text()
        self.assertIn('ENTRYPOINT ["aictl"]', dockerfile)


class TestPublishedPortsMatchTheClaims(unittest.TestCase):
    """Defects 4 and 5: 9999 unpublished, 8080 published but unserved."""

    def _published(self) -> set[str]:
        return set(re.findall(r'"(\d+):\d+"', _compose_text()))

    def test_every_documented_curl_port_is_published(self):
        text = _compose_text()
        documented = set(re.findall(r"localhost:(\d+)", text))
        self.assertTrue(documented, "header documents no curl")
        self.assertEqual(documented - self._published(), set(),
                         "a documented port is not published")

    def test_mock_engine_port_is_published(self):
        from aictl.core.constants import MOCK_ENGINE_PORT

        self.assertIn(str(MOCK_ENGINE_PORT), self._published())

    def test_daemon_port_is_published(self):
        from aictl.core.constants import DAEMON_PORT

        self.assertIn(str(DAEMON_PORT), self._published())

    def test_no_port_is_published_that_nothing_serves(self):
        # `demo` starts the mock engine and the daemon. It does not start the
        # proxy, so publishing 8080 advertised a service that never existed.
        from aictl.core.constants import DAEMON_PORT, MOCK_ENGINE_PORT

        served = {str(DAEMON_PORT), str(MOCK_ENGINE_PORT)}
        self.assertEqual(self._published() - served, set())

    def test_endpoint_count_in_the_header_is_current(self):
        match = re.search(r"(\d+) REST API endpoints", _compose_text())
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), rest_endpoint_count())


class TestDemoHostFlag(unittest.TestCase):
    """Defect 3, fixed without loosening the local default."""

    def _parse(self, *argv):
        from aictl.__main__ import build_parser

        return build_parser().parse_args(["demo", *argv])

    def test_default_is_loopback(self):
        # The security property. Nothing in the demo is authenticated, so the
        # default must not reach beyond this machine.
        from aictl.core.constants import DAEMON_HOST

        self.assertEqual(self._parse().host, DAEMON_HOST)
        self.assertEqual(DAEMON_HOST, "127.0.0.1")

    def test_host_can_be_overridden(self):
        self.assertEqual(self._parse("--host", "0.0.0.0").host, "0.0.0.0")

    def test_mock_engine_binds_the_host_it_is_given(self):
        from aictl.daemon.mock_engine import start_mock_engine

        for host in ("127.0.0.1", "0.0.0.0"):
            server = start_mock_engine(port=0, host=host)
            try:
                self.assertEqual(server.server_address[0], host)
            finally:
                server.shutdown()

    def test_mock_engine_defaults_to_loopback(self):
        from aictl.daemon.mock_engine import start_mock_engine

        server = start_mock_engine(port=0)
        try:
            self.assertEqual(server.server_address[0], "127.0.0.1")
        finally:
            server.shutdown()

    def test_demo_threads_the_host_into_both_servers(self):
        import inspect

        from aictl.cmd import demo

        source = inspect.getsource(demo.run)
        self.assertIn("host=host", source)          # mock engine
        self.assertIn("(host, daemon_port)", source)  # daemon


class TestBoundServerIsActuallyReachable(unittest.TestCase):
    """The property the container needs, exercised rather than asserted."""

    def test_a_zero_zero_bound_engine_answers_on_a_routable_address(self):
        import socket

        from aictl.daemon.mock_engine import start_mock_engine

        server = start_mock_engine(port=0, host="0.0.0.0")
        try:
            port = server.server_address[1]
            # Connect via the host's own LAN address, not loopback — this is
            # what a published container port does, and what 127.0.0.1-bound
            # servers refuse.
            address = socket.gethostbyname(socket.gethostname())
            with urllib.request.urlopen(
                    f"http://{address}:{port}/v1/models", timeout=5) as response:
                self.assertEqual(response.status, 200)
        finally:
            server.shutdown()

    def test_a_loopback_bound_engine_refuses_the_same_address(self):
        # Confirms the previous test proves something: the default really is
        # unreachable off-loopback, so --host is doing the work.
        import socket

        from aictl.daemon.mock_engine import start_mock_engine

        address = socket.gethostbyname(socket.gethostname())
        if address.startswith("127."):
            self.skipTest("host resolves to loopback; no off-loopback address")
        server = start_mock_engine(port=0, host="127.0.0.1")
        try:
            port = server.server_address[1]
            with self.assertRaises(Exception):
                urllib.request.urlopen(
                    f"http://{address}:{port}/v1/models", timeout=3)
        finally:
            server.shutdown()


class TestVersionCheckCoversTheGoPort(unittest.TestCase):
    """The residual of the July review's weakness 1, closed.

    Re-measuring that weakness showed it was overstated — it claimed a bump
    needs 20 file edits; only three files hold the version as a *value*
    (`core/constants.py`, `pyproject.toml`, `go-port/cmd/aictl/main.go`), the
    rest merely mention it in prose. But gate compared only the first two, so a
    bump that forgot the Go port left `aictl-go --version` reporting the
    previous release with nothing to catch it.
    """

    def test_all_three_sources_agree_today(self):
        from aictl.core.constants import AICTL_VERSION

        toml = re.search(r'version\s*=\s*"([^"]+)"',
                         Path("pyproject.toml").read_text()).group(1)
        go = re.search(r'version\s*=\s*"([^"]+)"',
                       Path("go-port/cmd/aictl/main.go").read_text()).group(1)
        self.assertEqual(toml, AICTL_VERSION)
        self.assertEqual(go, AICTL_VERSION)

    def test_gate_reads_the_go_version(self):
        import inspect

        from aictl.cmd import gate

        source = inspect.getsource(gate.run)
        self.assertIn("go-port", source)
        self.assertIn("main.go", source)

    def test_only_three_files_hold_the_version_as_a_value(self):
        # Pins the corrected measurement so the "20 files" estimate cannot
        # creep back, and so a fourth hardcoded copy is noticed.
        holders = set()
        for path in list(Path("aictl").rglob("*.py")) + [
                Path("pyproject.toml"), Path("go-port/cmd/aictl/main.go")]:
            for line in path.read_text(errors="replace").splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("//"):
                    continue
                if re.search(r'(?:version|VERSION)\s*=\s*"\d+\.\d+\.\d+"',
                             stripped):
                    holders.add(str(path))
        self.assertEqual(
            holders,
            {"aictl/core/constants.py", "pyproject.toml",
             "go-port/cmd/aictl/main.go"},
            f"the set of version-holding files changed: {sorted(holders)}")


class TestAutoModeStillExits(unittest.TestCase):
    """gate's demo phase depends on --auto terminating."""

    def test_auto_is_still_offered(self):
        from aictl.__main__ import build_parser

        self.assertTrue(build_parser().parse_args(["demo", "--auto"]).auto)


if __name__ == "__main__":
    unittest.main()

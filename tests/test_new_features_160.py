"""Pass 160: unpinned cosign keyless verification must warn, not silently pass.

長所短所改善点 finding: `verify_image`'s keyless path, when no
`certificate_identity`/`certificate_oidc_issuer` is given (which is the default
— `aictl model verify <ref>` exposed no CLI flag for either), falls back to:

    cosign verify --certificate-identity-regexp '.*' \
                  --certificate-oidc-issuer-regexp '.*' ...

which matches ANY valid Sigstore signature from ANY OIDC identity. An attacker
can self-sign a malicious image via their own free GitHub Actions workflow and
this check passes just as cleanly as a signature from the project's real
publisher — `verified=True` here means "signed by *someone*", not "signed by a
trusted publisher". `aictl model verify` printed a bare "✓ Signature verified"
with no indication the check provided no real provenance guarantee — a false
sense of security. (This is not a fabricated concern: it's the textbook cosign
keyless-verification-without-identity-pinning footgun.)

Fix (does not weaken any existing check — cosign's own verdict is unchanged):
  - `VerifyResult` gains a `warning` field, set whenever the wildcard-identity
    path is taken.
  - `--identity`/`--oidc-issuer` CLI flags let users actually pin the expected
    signer (real provenance verification).
  - `aictl model verify` surfaces `result.warning` prominently in BOTH human
    output (right after the checkmark, via `warn()`) and `--json`, and passes
    the new flags through to `verify_image`.
  - Providing `--key` (public-key mode) or both `--identity`+`--oidc-issuer`
    (pinned keyless) never sets the warning — only the wildcard fallback does.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import unittest
from unittest.mock import patch, MagicMock


def _fake_cosign(returncode=0, stdout="{}", stderr=""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestVerifyImageWarning(unittest.TestCase):
    def test_wildcard_path_sets_warning(self):
        from aictl.trust.cosign import verify_image
        with patch("aictl.trust.cosign.cosign_available", return_value=True):
            with patch("subprocess.run", return_value=_fake_cosign()):
                result = verify_image("ghcr.io/x/y:latest")
        self.assertTrue(result.verified)
        self.assertTrue(result.warning)
        self.assertIn("SOME", result.warning)

    def test_pinned_identity_no_warning(self):
        from aictl.trust.cosign import verify_image
        with patch("aictl.trust.cosign.cosign_available", return_value=True):
            with patch("subprocess.run", return_value=_fake_cosign()):
                result = verify_image(
                    "ghcr.io/x/y:latest",
                    certificate_identity="https://github.com/org/repo/.github/workflows/release.yml@refs/heads/main",
                    certificate_oidc_issuer="https://token.actions.githubusercontent.com",
                )
        self.assertTrue(result.verified)
        self.assertEqual(result.warning, "")

    def test_public_key_mode_no_warning(self):
        from aictl.trust.cosign import verify_image
        with patch("aictl.trust.cosign.cosign_available", return_value=True):
            with patch("subprocess.run", return_value=_fake_cosign()):
                result = verify_image("ghcr.io/x/y:latest", public_key="/tmp/key.pub")
        self.assertTrue(result.verified)
        self.assertEqual(result.warning, "")

    def test_cosign_unavailable_no_warning(self):
        # No verification attempted at all; no misleading warning either.
        from aictl.trust.cosign import verify_image
        with patch("aictl.trust.cosign.cosign_available", return_value=False):
            result = verify_image("ghcr.io/x/y:latest")
        self.assertFalse(result.verified)
        self.assertEqual(result.warning, "")

    def test_only_identity_without_issuer_still_wildcards_and_warns(self):
        # Both must be supplied to pin; a partial pin still falls to wildcard.
        from aictl.trust.cosign import verify_image
        with patch("aictl.trust.cosign.cosign_available", return_value=True):
            with patch("subprocess.run", return_value=_fake_cosign()):
                result = verify_image("ghcr.io/x/y:latest",
                                      certificate_identity="someone@example.com")
        self.assertTrue(result.warning)


class TestModelVerifyCliSurfacesWarning(unittest.TestCase):
    def _run(self, identity="", oidc_issuer=""):
        from aictl.cmd import model
        args = argparse.Namespace(reference="ghcr.io/x/y:latest", key="",
                                  identity=identity, oidc_issuer=oidc_issuer, json=False)
        out, errbuf = io.StringIO(), io.StringIO()
        with patch("aictl.trust.cosign.cosign_available", return_value=True):
            with patch("subprocess.run", return_value=_fake_cosign()):
                with contextlib.redirect_stdout(out):
                    with contextlib.redirect_stderr(errbuf):
                        code = model.run_verify(args)
        return code, out.getvalue() + errbuf.getvalue()

    def test_human_output_shows_warning(self):
        code, text = self._run()
        self.assertEqual(code, 0)
        self.assertIn("SOME Sigstore identity", text)

    def test_pinned_identity_no_warning_in_output(self):
        code, text = self._run(identity="me@example.com", oidc_issuer="https://issuer")
        self.assertNotIn("SOME Sigstore identity", text)

    def test_json_output_includes_warning_field(self):
        from aictl.cmd import model
        args = argparse.Namespace(reference="ghcr.io/x/y:latest", key="",
                                  identity="", oidc_issuer="", json=True)
        out = io.StringIO()
        with patch("aictl.trust.cosign.cosign_available", return_value=True):
            with patch("subprocess.run", return_value=_fake_cosign()):
                with contextlib.redirect_stdout(out):
                    model.run_verify(args)
        text = out.getvalue()
        data = json.loads(text[text.index("{"):])   # skip the leading "✓ Verifying..." line
        self.assertIn("warning", data)
        self.assertTrue(data["warning"])

    def test_identity_flags_registered(self):
        p = argparse.ArgumentParser(prog="aictl")
        sub = p.add_subparsers()
        from aictl.cmd import model
        model.register(sub)
        ns = p.parse_args(["model", "verify", "ref:latest",
                           "--identity", "me@example.com",
                           "--oidc-issuer", "https://issuer"])
        self.assertEqual(ns.identity, "me@example.com")
        self.assertEqual(ns.oidc_issuer, "https://issuer")


if __name__ == "__main__":
    unittest.main()

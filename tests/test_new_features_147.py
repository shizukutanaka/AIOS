"""Pass 147: digest verification must be format-insensitive (not falsely reject).

`sha256_file` always returns 'sha256:<lowercase hex>', but a digest supplied for
verification legitimately arrives in other equivalent forms depending on the
registry/tool that emitted it:

  - bare hex with no algorithm prefix
  - UPPERCASE hex (SHA-256 hex is case-insensitive)
  - 'SHA256:' / surrounding whitespace

`verify_digest` compared with a strict `actual == expected`, so every one of
those EQUIVALENT digests was treated as a MISMATCH — and under TrustPolicy
'enforce' that rejects a VALID model (a trust regression that fails closed but
breaks legitimate enforcement). Verified: enforce mode rejected a correct
bare-hex digest.

Fix: `_canonical_digest` folds case and the optional 'sha256:' prefix on BOTH
sides before comparison. This does not weaken the check — the full 64-char hex
body must still match exactly (wrong/truncated digests still fail), and the
no-digest ("no policy") path is unchanged.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from aictl.trust.verify import sha256_file, verify_digest, TrustPolicy


class _TmpFile:
    def __enter__(self):
        self.path = tempfile.mktemp()
        with open(self.path, "wb") as f:
            f.write(b"model-weights-content-v147")
        self.canonical = sha256_file(self.path)        # sha256:<lower hex>
        self.hex = self.canonical.split(":", 1)[1]
        return self

    def __exit__(self, *a):
        os.remove(self.path)


class TestDigestFormatTolerance(unittest.TestCase):
    def test_bare_hex_matches(self):
        with _TmpFile() as t:
            self.assertTrue(verify_digest(t.path, t.hex))

    def test_uppercase_matches(self):
        with _TmpFile() as t:
            self.assertTrue(verify_digest(t.path, t.canonical.upper()))

    def test_prefixed_uppercase_matches(self):
        with _TmpFile() as t:
            self.assertTrue(verify_digest(t.path, "SHA256:" + t.hex.upper()))

    def test_surrounding_whitespace_matches(self):
        with _TmpFile() as t:
            self.assertTrue(verify_digest(t.path, f"  {t.canonical}\n"))

    def test_canonical_still_matches(self):
        with _TmpFile() as t:
            self.assertTrue(verify_digest(t.path, t.canonical))


class TestDigestSecurityPreserved(unittest.TestCase):
    def test_wrong_digest_rejected(self):
        with _TmpFile() as t:
            self.assertFalse(verify_digest(t.path, "sha256:" + "0" * 64))

    def test_truncated_digest_rejected(self):
        with _TmpFile() as t:
            self.assertFalse(verify_digest(t.path, t.hex[:32]))

    def test_off_by_one_char_rejected(self):
        with _TmpFile() as t:
            flipped = ("f" if t.hex[0] != "f" else "e") + t.hex[1:]
            self.assertFalse(verify_digest(t.path, flipped))

    def test_empty_is_no_policy(self):
        with _TmpFile() as t:
            self.assertTrue(verify_digest(t.path, ""))


class TestEnforceModeAcceptsValidVariants(unittest.TestCase):
    def test_enforce_accepts_bare_hex(self):
        with _TmpFile() as t:
            ok, msg = TrustPolicy("enforce").check(t.path, t.hex)
            self.assertTrue(ok, msg)

    def test_enforce_still_rejects_wrong(self):
        with _TmpFile() as t:
            ok, _ = TrustPolicy("enforce").check(t.path, "sha256:" + "0" * 64)
            self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()

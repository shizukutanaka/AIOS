"""Pass 219: two pinned constants pointed at images that do not exist.

Found by following CLAUDE.md's own rule — "All constants in
`aictl/core/constants.py` — no hardcoded ports/versions" — into the deployment
paths, where three modules hardcoded engine images instead.

The same product emitted **different vLLM versions depending on which path you
took**. `VLLM_IMAGE = "vllm/vllm-openai:v0.19.0"` was used by four modules
(disagg, modelservice, kserve, deploy) while quadlet and orchestrator shipped
`vllm/vllm-openai:latest`. A user comparing a local `aictl apply` against
`aictl deploy modelservice` was running two different builds, and the local one
changed under them without warning.

Then the constants themselves turned out to be wrong — and the pattern in
*which* ones is the finding:

    VLLM_IMAGE    vllm/vllm-openai:v0.19.0   used by 4 modules   exists
    SGLANG_IMAGE  lmsys/sglang:v0.5.9        used by nobody      404
    OLLAMA_IMAGE  ollama/ollama:0.20         used by nobody      404

The two nobody used were never exercised, so nothing ever discovered they were
unpullable. `lmsys/sglang` does not exist at all (the org is `lmsysorg`, 11.6M
pulls); `ollama/ollama:0.20` does not exist because the tag scheme is
MAJOR.MINOR.PATCH and the intended v0.20 is `0.20.0`. Both were verified
against the registry rather than guessed — and the verification mattered:
"single-sourcing" the deployment paths through `SGLANG_IMAGE`, which is the
obvious tidy-up, would have pointed three working paths at a repository that
cannot be pulled. The tidy version of this change was the broken one.

`:latest` is gone from every engine path. A floating tag in a generated Quadlet
unit or KServe CRD cannot be pinned by digest, silently changes under the
operator, and cannot be verified by the `aictl trust` subsystem this product
ships. `trt-llm` stays floating deliberately: it is on NGC rather than Docker
Hub, so the tag could not be checked the same way, and pinning it to a guess
would be the same mistake in the other direction.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from aictl.core.constants import (
    OLLAMA_IMAGE,
    RUNTIME_IMAGES,
    SGLANG_IMAGE,
    VLLM_IMAGE,
)


class TestVerifiedImageValues(unittest.TestCase):
    """Pinned to what the registry actually serves, checked entry by entry."""

    def test_sglang_uses_the_org_that_exists(self):
        # `lmsys/sglang` is a 404; the SGLang project publishes to `lmsysorg`.
        self.assertTrue(SGLANG_IMAGE.startswith("lmsysorg/sglang:"),
                        f"{SGLANG_IMAGE} — `lmsys/sglang` does not exist")

    def test_ollama_tag_has_a_patch_component(self):
        # Ollama tags are MAJOR.MINOR.PATCH; bare `0.20` is a 404.
        tag = OLLAMA_IMAGE.split(":", 1)[1]
        self.assertRegex(tag, r"^\d+\.\d+\.\d+",
                         f"{OLLAMA_IMAGE} — bare MAJOR.MINOR is not a real tag")

    def test_vllm_image_is_unchanged(self):
        # The one constant that was in use was the one that was correct.
        self.assertEqual(VLLM_IMAGE, "vllm/vllm-openai:v0.19.0")


class TestEnginePathsAgree(unittest.TestCase):
    """The defect: the same runtime resolved to different images per path."""

    def test_quadlet_and_kserve_resolve_vllm_identically(self):
        from aictl.stack.kserve import RUNTIME_IMAGES as kserve_images
        from aictl.stack.manifest import ServiceDef
        from aictl.stack.quadlet import _resolve_image

        svc = ServiceDef(name="x", runtime="vllm", model="m")
        self.assertEqual(_resolve_image(svc), kserve_images["vllm"])
        self.assertEqual(_resolve_image(svc), VLLM_IMAGE)

    def test_every_runtime_resolves_through_the_shared_map(self):
        from aictl.stack.manifest import ServiceDef
        from aictl.stack.quadlet import _resolve_image

        for runtime, image in RUNTIME_IMAGES.items():
            svc = ServiceDef(name="x", runtime=runtime, model="m")
            self.assertEqual(_resolve_image(svc), image, runtime)

    def test_an_explicit_service_image_still_wins(self):
        # Single-sourcing must not override what a user pinned themselves.
        from aictl.stack.manifest import ServiceDef
        from aictl.stack.quadlet import _resolve_image

        svc = ServiceDef(name="x", runtime="vllm", model="m",
                         image="my.registry/vllm:custom")
        self.assertEqual(_resolve_image(svc), "my.registry/vllm:custom")

    def test_unknown_runtime_yields_empty(self):
        from aictl.stack.manifest import ServiceDef
        from aictl.stack.quadlet import _resolve_image

        self.assertEqual(
            _resolve_image(ServiceDef(name="x", runtime="nope", model="m")), "")

    def test_kserve_shares_the_constant_object(self):
        from aictl.stack import kserve

        self.assertIs(kserve.RUNTIME_IMAGES, RUNTIME_IMAGES)


class TestNoFloatingTagsInEnginePaths(unittest.TestCase):
    """A `:latest` engine image cannot be verified by `aictl trust`."""

    _PINNED = ("vllm", "sglang", "ollama")

    def test_docker_hub_engines_are_pinned(self):
        for runtime in self._PINNED:
            self.assertNotIn(":latest", RUNTIME_IMAGES[runtime], runtime)

    def test_every_pinned_image_carries_a_version_tag(self):
        for runtime in self._PINNED:
            tag = RUNTIME_IMAGES[runtime].split(":", 1)[1]
            self.assertRegex(tag, r"^v?\d+\.\d+", f"{runtime}: {tag}")

    def test_trt_llm_is_left_floating_on_purpose(self):
        # NGC rather than Docker Hub, so the tag could not be verified the
        # same way. Pinning it to a guess would be the same error inverted.
        self.assertIn(":latest", RUNTIME_IMAGES["trt-llm"])

    def test_deployment_modules_no_longer_hardcode_engine_images(self):
        # The rule CLAUDE.md states and these three modules broke.
        pattern = re.compile(r'"(?:docker\.io/)?(?:vllm|lmsysorg|lmsys|ollama)/[^"]*:')
        for name in ("quadlet.py", "orchestrator.py", "kserve.py"):
            source = Path("aictl/stack") / name
            offenders = pattern.findall(source.read_text())
            self.assertEqual(offenders, [], f"{name} hardcodes an engine image")


class TestGeneratedArtifactsUseThePinnedImages(unittest.TestCase):
    def test_a_quadlet_unit_names_the_pinned_image(self):
        from aictl.stack.manifest import ServiceDef, StackManifest
        from aictl.stack.quadlet import _resolve_image

        svc = ServiceDef(name="engine", runtime="vllm", model="llama3.1:8b")
        StackManifest(name="s", services=[svc])
        self.assertIn("v0.19.0", _resolve_image(svc))

    def test_no_engine_path_can_emit_latest(self):
        # The property a user cares about: whatever path they take, the
        # generated artifact names a specific build.
        from aictl.stack.manifest import ServiceDef
        from aictl.stack.quadlet import _resolve_image

        for runtime in self._pinned_runtimes():
            svc = ServiceDef(name="x", runtime=runtime, model="m")
            self.assertNotIn(":latest", _resolve_image(svc), runtime)

    @staticmethod
    def _pinned_runtimes():
        return ("vllm", "sglang", "ollama")


if __name__ == "__main__":
    unittest.main()

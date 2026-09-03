from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hearth.imagegen.acceptance import ImageAcceptance
from hearth.observation.identity import DispatchIdentity
from hearth.toolsurface.image_generate import submit_image


class ImageGenerateToolTest(unittest.TestCase):
    def test_text_priority_maps_to_numeric_execution_policy(self) -> None:
        calls = []
        service = SimpleNamespace(
            _image_dispatcher=SimpleNamespace(
                session=SimpleNamespace(status=lambda: {"session": {"state": "llm"}})
            ),
            submit=lambda **kwargs: calls.append(kwargs) or {
                "job_id": "job_test", "status": "queued"
            },
        )
        registry = {"schema": "imagegen.workflow-registry.v1", "workflows": [{
            "id": "z-image-turbo", "enabled": True, "cards_required": 1,
            "allowed_strategies": ["single"],
        }]}
        with patch("hearth.toolsurface.image_generate.current_identity", return_value=
                   DispatchIdentity("caller", "local", "omen")), patch(
            "hearth.toolsurface.image_generate.get_execution_service", return_value=service
        ), patch(
            "hearth.toolsurface.image_generate._workflow_registry", return_value=registry
        ), patch(
            "hearth.toolsurface.image_generate.image_acceptance.load_acceptance",
            return_value=ImageAcceptance(),
        ):
            result = submit_image(
                "z-image-turbo", {"prompt": "private", "seed": 1},
                strategy="single", priority="high", deadline_s=60,
            )
        self.assertTrue(result["ok"])
        self.assertEqual({"priority": 1, "deadline_s": 60}, calls[0]["policy"])

    def test_unqualified_dual_strategy_is_refused_before_ledger_submission(self) -> None:
        registry = {"schema": "imagegen.workflow-registry.v1", "workflows": [{
            "id": "sdxl-base-dual-layers", "enabled": True, "cards_required": 2,
            "allowed_strategies": ["dual_layers"],
        }]}
        with patch("hearth.toolsurface.image_generate.current_identity", return_value=
                   DispatchIdentity("caller", "local", "omen")), patch(
            "hearth.toolsurface.image_generate._workflow_registry", return_value=registry
        ), patch(
            "hearth.toolsurface.image_generate.image_acceptance.load_acceptance",
            return_value=ImageAcceptance(),
        ):
            with self.assertRaisesRegex(ValueError, "not production-qualified"):
                submit_image(
                    "sdxl-base-dual-layers", {"prompt": "private", "seed": 1},
                    strategy="dual_layers",
                )


if __name__ == "__main__":
    unittest.main()

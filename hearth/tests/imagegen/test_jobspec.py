from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hearth.imagegen.jobspec import (
    ImageArgumentError,
    parse_image_arguments,
    validate_image_arguments,
)


class ImageJobSpecTest(unittest.TestCase):
    def test_random_seed_is_resolved_before_dispatch(self) -> None:
        with patch("hearth.imagegen.jobspec.secrets.randbelow", return_value=41):
            spec = parse_image_arguments({
                "workflow_id": "z-image-turbo",
                "parameters": {"prompt": "secret", "seed": -1},
            })
        self.assertEqual(42, spec.parameters["seed"])

    def test_private_prompt_is_only_in_packed_artifact(self) -> None:
        operation = SimpleNamespace(max_prompt_bytes=256 * 1024)
        normalized, packed = validate_image_arguments(operation, {
            "workflow_id": "z-image-turbo",
            "parameters": {
                "prompt": "do not ledger this prompt",
                "negative_prompt": "nor this",
                "seed": 7,
                "width": 1024,
            },
            "strategy": "single",
            "priority": "normal",
        })
        self.assertNotIn("do not ledger this prompt", json.dumps({
            key: value for key, value in normalized.items() if key != "_spec"
        }))
        self.assertNotIn("negative_prompt", normalized["parameters"])
        self.assertIn("prompt_sha256", normalized["parameters"])
        self.assertIn(b"do not ledger this prompt", packed)

    def test_rejects_paths_and_unregistered_argument_names(self) -> None:
        with self.assertRaises(ImageArgumentError):
            parse_image_arguments({
                "workflow_id": "z-image-turbo",
                "parameters": {"prompt": "x", "seed": 1, "model_path": "E:\\model.gguf"},
            })
        with self.assertRaises(ImageArgumentError):
            parse_image_arguments({
                "workflow_id": "z-image-turbo",
                "parameters": {"prompt": "x", "seed": 1},
                "command_line": "--anything",
            })

    def test_target_lane_is_top_level_and_dual_targeting_is_refused(self) -> None:
        spec = parse_image_arguments({
            "workflow_id": "wan2-i2v",
            "parameters": {"prompt": "x", "input_image": "still.png", "seed": 1},
            "strategy": "single", "target_lane": "b70@bus9",
        })
        self.assertEqual("b70@bus9", spec.target_lane)
        self.assertEqual("b70@bus9", spec.to_dict()["target_lane"])
        with self.assertRaises(ImageArgumentError):
            parse_image_arguments({
                "workflow_id": "dual", "parameters": {"prompt": "x", "seed": 1},
                "strategy": "dual_cfg", "target_lane": "b70@bus4",
            })


if __name__ == "__main__":
    unittest.main()

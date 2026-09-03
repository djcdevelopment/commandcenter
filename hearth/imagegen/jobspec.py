"""Validation and private-input packing for ``image.generate``."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any, Mapping

WORKFLOW_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,127}$")
STRATEGIES = frozenset({"auto", "single", "dual_cfg", "dual_layers"})
PRIORITIES = frozenset({"low", "normal", "high"})
TARGET_LANES = frozenset({"any", "b70@bus4", "b70@bus9"})
MAX_INPUT_BYTES = 256 * 1024


class ImageArgumentError(ValueError):
    pass


@dataclass(frozen=True)
class ImageJobSpec:
    workflow_id: str
    parameters: dict[str, Any]
    strategy: str
    priority: str
    target_lane: str = "any"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "imagegen.request.v1",
            "workflow_id": self.workflow_id,
            "parameters": self.parameters,
            "strategy": self.strategy,
            "priority": self.priority,
            "target_lane": self.target_lane,
        }


def _is_absolute_path(value: str) -> bool:
    normalized = value.replace("/", "\\")
    return normalized.startswith("\\\\") or (
        len(normalized) >= 3 and normalized[1:3] == ":\\"
    )


def parse_image_arguments(arguments: Mapping[str, Any]) -> ImageJobSpec:
    value = dict(arguments)
    allowed = {"workflow_id", "parameters", "strategy", "priority", "target_lane"}
    unknown = set(value) - allowed
    if unknown:
        raise ImageArgumentError(
            "unknown image.generate arguments: %s" % ", ".join(sorted(unknown))
        )
    workflow_id = value.get("workflow_id")
    if not isinstance(workflow_id, str) or not WORKFLOW_RE.fullmatch(workflow_id):
        raise ImageArgumentError("workflow_id must be a lowercase registry identifier")
    parameters = value.get("parameters")
    if not isinstance(parameters, dict):
        raise ImageArgumentError("parameters must be an object")
    prompt = parameters.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ImageArgumentError("parameters.prompt must be a non-empty string")
    for key, item in parameters.items():
        if not isinstance(key, str) or not key or key.startswith("_"):
            raise ImageArgumentError("parameter names must be public non-empty strings")
        if key.lower().endswith(("_path", "_file", "_directory")):
            raise ImageArgumentError("paths are supplied by registered workflows, not callers")
        if isinstance(item, str) and _is_absolute_path(item):
            raise ImageArgumentError("absolute paths are not accepted in image parameters")
    strategy = value.get("strategy", "auto")
    if strategy not in STRATEGIES:
        raise ImageArgumentError("strategy must be one of: %s" % ", ".join(sorted(STRATEGIES)))
    priority = value.get("priority", "normal")
    if priority not in PRIORITIES:
        raise ImageArgumentError("priority must be one of: low, normal, high")
    target_lane = value.get("target_lane", "any")
    if target_lane not in TARGET_LANES:
        raise ImageArgumentError(
            "target_lane must be one of: %s" % ", ".join(sorted(TARGET_LANES))
        )
    if target_lane != "any" and strategy in {"dual_cfg", "dual_layers"}:
        raise ImageArgumentError("target_lane is only valid for single-card strategies")

    resolved = dict(parameters)
    seed = resolved.get("seed", -1)
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ImageArgumentError("parameters.seed must be an integer")
    if seed == -1:
        seed = secrets.randbelow(2**63 - 2) + 1
    if seed < 0 or seed >= 2**63:
        raise ImageArgumentError("parameters.seed must be -1 or a signed 63-bit value")
    resolved["seed"] = seed
    try:
        encoded = json.dumps(resolved, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ImageArgumentError("parameters must contain JSON values") from exc
    if len(encoded) > MAX_INPUT_BYTES:
        raise ImageArgumentError("image parameters exceed the 256 KiB limit")
    return ImageJobSpec(workflow_id, resolved, str(strategy), str(priority), str(target_lane))


def validate_image_arguments(operation, arguments: Mapping[str, Any]) -> tuple[dict, bytes]:
    spec = parse_image_arguments(arguments)
    packed = json.dumps(
        spec.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    if len(packed) > operation.max_prompt_bytes:
        raise ImageArgumentError("packed image request exceeds operation input limit")
    private = {"prompt", "negative_prompt"}
    public_parameters = {
        key: item for key, item in spec.parameters.items() if key not in private
    }
    public_parameters["prompt_sha256"] = hashlib.sha256(
        spec.parameters["prompt"].encode("utf-8")
    ).hexdigest()
    normalized = {
        "workflow_id": spec.workflow_id,
        "parameters": public_parameters,
        "strategy": spec.strategy,
        "priority": spec.priority,
        "target_lane": spec.target_lane,
        # LOAD-BEARING CONTRACT: `_spec` carries the UNREDACTED spec, prompt included, for
        # the dispatcher. It is kept out of the ledger by the underscore-prefix filter in
        # hearth/execution/service.py (the same convention hearth/media/jobspec.py uses).
        # Anything that ledgers `normalized` wholesale re-exposes the private prompt --
        # test_service.py locks this; do not weaken it.
        "_spec": spec.to_dict(),
    }
    return normalized, packed

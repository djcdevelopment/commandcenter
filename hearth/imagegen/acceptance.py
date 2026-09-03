"""Conservative production acceptance for the Windows image agent."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ImageAcceptance:
    accepted_lane_count: int = 1
    dual_cfg_enabled: bool = False
    dual_layers_enabled: bool = False
    stale: bool = True
    detail: str = "No accepted image-generation qualification record."

    def to_dict(self) -> dict:
        return {
            "schema": "imagegen.acceptance.v1",
            "accepted_lane_count": self.accepted_lane_count,
            "dual_cfg_enabled": self.dual_cfg_enabled,
            "dual_layers_enabled": self.dual_layers_enabled,
            "stale": self.stale,
            "detail": self.detail,
        }


def default_path() -> Path:
    configured = os.environ.get("IMAGEGEN_ACCEPTANCE")
    return Path(configured).expanduser().resolve() if configured else Path(
        r"E:\omen\imagegen\config\acceptance.json"
    )


def load_acceptance(path: Optional[Path] = None) -> ImageAcceptance:
    target = path or default_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ImageAcceptance(detail="image acceptance unavailable: %s" % exc)
    count = raw.get("accepted_lane_count") if isinstance(raw, dict) else None
    if (not isinstance(raw, dict) or raw.get("schema") != "imagegen.acceptance.v1" or
            isinstance(count, bool) or not isinstance(count, int) or count not in (1, 2)):
        return ImageAcceptance(detail="image acceptance has an unsupported schema or lane count")
    return ImageAcceptance(
        accepted_lane_count=count,
        dual_cfg_enabled=bool(raw.get("dual_cfg_enabled", False)),
        dual_layers_enabled=bool(raw.get("dual_layers_enabled", False)),
        stale=bool(raw.get("stale", True)),
        detail=str(raw.get("detail", "")),
    )


def workflow_available(workflow: dict, strategy: str, acceptance: ImageAcceptance) -> bool:
    cards = workflow.get("cards_required", 1)
    if isinstance(cards, bool) or not isinstance(cards, int):
        return False
    if cards > acceptance.accepted_lane_count:
        return False
    effective = strategy
    if strategy == "auto":
        allowed = workflow.get("allowed_strategies") or []
        effective = allowed[0] if cards > 1 and allowed else "single"
    if effective == "dual_cfg" and not acceptance.dual_cfg_enabled:
        return False
    if effective == "dual_layers" and not acceptance.dual_layers_enabled:
        return False
    return True

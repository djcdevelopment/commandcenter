#!/usr/bin/env python3
"""generate_qualification_brief — emit experiment briefs for configurations with empty capability cells.

Reads the qualification capability matrix (research/state/capability_matrix.json) and
configuration metadata (host/lab-configurations.toml). Finds configurations with unrecorded
qualification cells and generates a self-contained experiment brief for the night loop:
swapping the seat drop-in, running the qualification campaign, and restoring resident configuration.

Per ADR-0052 and Task 11:
- Emits at most ONE experiment configuration brief per night.
- Generates valid CCMETA header + experiment fields.

Usage:
    generate_qualification_brief.py [--configuration <name>] [--out-dir <dir>] [--stdout]
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tomllib
from typing import Any, Optional

HOME = Path.home()
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MATRIX_PATH = HOME / "work" / "lab-rnd" / "research" / "state" / "capability_matrix.json"
DEFAULT_CONFIGS_TOML = REPO_ROOT / "host" / "lab-configurations.toml"
DEFAULT_OUT_DIR = HOME / "hearth-production" / "var" / "backlog" / "queued"

# Known drop-in mapping for OMEN seat 0 profiles
SEAT0_DROPIN_MAP: dict[str, dict[str, Any]] = {
    "seat0-qwen3-32b": {
        "seat": 0,
        "dropin": "stage5-qwen3-32b.conf",
        "expect_model": "qwen3-32b",
        "description": "Seat 0 Qwen3-32B experiment, AM4 dense-tp2",
    },
    "seat0-devstral2507": {
        "seat": 0,
        "dropin": "stage7-devstral2507.conf",
        "expect_model": "devstral-2507",
        "description": "Seat 0 Devstral 2507 experiment, AM4 dense-tp2",
    },
    "seat0-gemma4": {
        "seat": 0,
        "dropin": "stage4-gemma4.conf",
        "expect_model": "gemma-4-31b",
        "description": "Seat 0 Gemma 4 31B experiment, AM4 dense-tp2",
    },
    "seat0-devstral-small-2": {
        "seat": 0,
        "dropin": "stage6-devstral.conf",
        "expect_model": "devstral-small-2",
        "description": "Seat 0 Devstral Small 2 24B experiment, AM4 dense-tp2",
    },
}


def load_matrix(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"capability matrix not found at {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# Task keys that must never be rerun (presence_probe is closed negative)
NON_RUNNABLE_TASKS = {"presence_probe"}

# Configurations excluded from automatic night qualification
EXCLUDED_CONFIGURATIONS = {"day", "seat0-qwen3-32b"}


def find_unrecorded_configurations(matrix_data: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """Return list of (configuration_name, [unrecorded_task_keys]) sorted by configuration."""
    matrix = matrix_data.get("matrix", {})
    all_tasks = list(matrix_data.get("tasks", {}).keys())
    results: list[tuple[str, list[str]]] = []

    for cfg in matrix_data.get("configurations", []):
        if cfg in EXCLUDED_CONFIGURATIONS:
            continue
        cells = matrix.get(cfg, {})
        unrecorded = [t for t in all_tasks if t not in cells and t not in NON_RUNNABLE_TASKS]
        if unrecorded:
            results.append((cfg, unrecorded))
    return results


def render_brief(
    cfg: str,
    unrecorded_tasks: list[str],
    dropin_info: dict[str, Any],
    campaign_cmd: str,
    max_minutes: int = 180,
    restore_by: str = "06:30",
) -> str:
    seat = dropin_info.get("seat", 0)
    dropin = dropin_info.get("dropin", f"{cfg}.conf")
    expect_model = dropin_info.get("expect_model", cfg)
    task_list_str = ", ".join(unrecorded_tasks)

    header = json.dumps({
        "builders": ["omen-experiment"],
        "task_class": "experiment",
        "est_tokens": 1000,
        "requires": ["state.json"],
        "max_age_s": 86400,
    })

    return f"""<!-- CCMETA
{header}
-->
seat: {seat}
dropin: {dropin}
expect_model: {expect_model}
campaign: {campaign_cmd}
max_minutes: {max_minutes}
restore_by: {restore_by}
work: qitem-qualification-{cfg}
---
Run qualification campaign for lab configuration `{cfg}`.

This configuration has {len(unrecorded_tasks)} unrecorded qualification task cells in the capability matrix:
{task_list_str}

The experiment lifecycle manages:
1. Tenancy acquisition on omen-b70-pool.
2. Snapshot of active drop-ins and served models on seat {seat}.
3. Application of staged drop-in `{dropin}` and restart/verification of model `{expect_model}`.
4. Execution of the qualification campaign via `{campaign_cmd}`.
5. Clean removal of `{dropin}`, restoration of resident drop-ins, restart/verification of resident model.
6. Tenancy release and restoration of resident state.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--matrix-file", type=Path, default=DEFAULT_MATRIX_PATH, help="Path to capability_matrix.json")
    ap.add_argument("--configuration", "-c", type=str, default=None, help="Specific configuration to generate brief for")
    ap.add_argument("--out-dir", "-o", type=Path, default=None, help="Directory to write brief file to (default: print or queued)")
    ap.add_argument("--stdout", action="store_true", help="Print generated brief to stdout")
    ap.add_argument("--max-minutes", type=int, default=180, help="Maximum execution minutes (default: 180)")
    ap.add_argument("--restore-by", type=str, default="06:30", help="Time by which seat must be restored (default: 06:30)")
    ap.add_argument("--campaign-cmd", type=str, default=None, help="Override campaign command")
    args = ap.parse_args()

    matrix_data = load_matrix(args.matrix_file)
    candidates = find_unrecorded_configurations(matrix_data)

    if not candidates:
        print("All configurations have fully recorded capability cells. Nothing to qualify.", file=sys.stderr)
        return 0

    target_cfg: str
    target_tasks: list[str]

    if args.configuration:
        match = [c for c in candidates if c[0] == args.configuration]
        if not match:
            # Check if configuration exists in matrix but has no empty cells
            if args.configuration in matrix_data.get("configurations", []):
                print(f"Configuration '{args.configuration}' has no unrecorded cells.", file=sys.stderr)
                return 0
            print(f"Configuration '{args.configuration}' not found in matrix configurations.", file=sys.stderr)
            return 1
        target_cfg, target_tasks = match[0]
    else:
        # Default: pick first candidate that has a known dropin mapping
        supported = [c for c in candidates if c[0] in SEAT0_DROPIN_MAP]
        if not supported:
            target_cfg, target_tasks = candidates[0]
        else:
            target_cfg, target_tasks = supported[0]

    dropin_info = SEAT0_DROPIN_MAP.get(target_cfg)
    if not dropin_info:
        print(f"Warning: Configuration '{target_cfg}' has no predefined dropin mapping.", file=sys.stderr)
        dropin_info = {"seat": 0, "dropin": f"{target_cfg}.conf", "expect_model": target_cfg}

    campaign_cmd = args.campaign_cmd or f"/home/derek/bin/run-qualification-campaign {target_cfg}"
    brief_content = render_brief(
        cfg=target_cfg,
        unrecorded_tasks=target_tasks,
        dropin_info=dropin_info,
        campaign_cmd=campaign_cmd,
        max_minutes=args.max_minutes,
        restore_by=args.restore_by,
    )

    if args.stdout or (args.out_dir is None and not args.stdout):
        print(brief_content)

    if args.out_dir:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        filename = f"{today}-exp-qualify-{target_cfg}.md"
        out_path = args.out_dir / filename
        out_path.write_text(brief_content, encoding="utf-8")
        print(f"Wrote qualification brief to {out_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

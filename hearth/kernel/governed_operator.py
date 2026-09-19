"""Fail-closed tool/argument policy for the CPU-only Hermes fleet controller."""
from pathlib import Path
import os
import importlib.util
import json
import subprocess


def qualify_builders() -> None:
    """Fresh trusted worker config + passive native readiness, never a saved boolean."""
    helper = Path(__file__).resolve().parents[2] / "fleet/hermes/builder-access.py"
    spec = importlib.util.spec_from_file_location("hermes_builder_access", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = """import sys,json
sys.path.insert(0,'/home/claude/fleet-worker-node/scripts')
from runner_presets import resolve
cfg,public=resolve('/home/claude/fleet-worker-node','am4-shared-27b')
print(json.dumps(public))
"""
    for builder in ("cc-builder-2", "cc-builder-3"):
        result = subprocess.run(module.command(builder, source), capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise PermissionError("fresh AM4 preset qualification failed for " + builder)
        record = json.loads(result.stdout)
        if record.get("preset") != "am4-shared-27b" or record.get("context_length") != 131072:
            raise PermissionError("worker route qualification mismatch")

READ_TOOLS = {"kernel_status", "query_knowledge", "query_beliefs_summary",
              "query_rung_state", "rotation_status", "read_file", "list_dir",
              "glob_files", "git_status", "git_log", "capture_resource_snapshot",
              "propose_schedule", "queue_status", "task_status", "get_build_request",
              "list_build_requests"}
WRITE_TOOLS = {"create_build_request", "execute_build_request", "update_build_request",
               "close_build_request", "submit_task"}


def check_governed_call(profile: str, name: str, args: dict) -> None:
    if profile != "governed-operator":
        return
    if name not in READ_TOOLS | WRITE_TOOLS:
        raise PermissionError("governed operator: tool not approved")
    if name == "read_file" and not 1 <= args.get("max_bytes", 200000) <= 200000:
        raise PermissionError("bounded source reads required")
    if args.get("backend") not in (None, "am4-dense"):
        raise PermissionError("governed operator: local-only backend")
    if any(args.get(key) is not None for key in ("receipt_dir", "submit_fn", "status_fn", "harvest_fn", "list_files_fn")):
        raise PermissionError("governed operator: internal storage/test overrides denied")
    if name == "create_build_request":
        root = os.environ.get("HERMES_BUILD_REPO")
        if not root or Path(args.get("repo", "")).resolve() != Path(root).resolve():
            raise PermissionError("governed operator: explicit approved build repo required")
        if args.get("execute"):
            raise PermissionError("create a receipt before dispatch")
        args["backend"] = "am4-dense"
    if name in {"execute_build_request", "submit_task"}:
        if name == "submit_task":
            raise PermissionError("Hermes dispatch requires an idempotent build receipt")
        if name == "execute_build_request" and args.get("mode") != "delegate":
            raise PermissionError("controller records delegated work, not agent self-execution")
        if args.get("builders") != ["cc-builder-2", "cc-builder-3"]:
            raise PermissionError("exact qualified local builder pair required; no auto-padding")
        if args.get("promotion_policy") not in (None, "manual") or args.get("runner_preset") not in (None, "am4-shared-27b"):
            raise PermissionError("Hermes cannot override review policy or runner preset")
        age = args.get("max_age_s")
        if type(age) is not int or not 60 <= age <= 2700:
            raise PermissionError("delegated lifetime must be 60..2700 seconds")
        qualify_builders()
        args.update(promotion_policy="manual", runner_preset="am4-shared-27b", operator="hermes", backend="am4-dense")

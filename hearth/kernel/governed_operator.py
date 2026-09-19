"""Fail-closed tool/argument policy for the CPU-only Hermes fleet controller."""
from pathlib import Path
import os

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
    if args.get("backend") not in (None, "am4-dense-27b", "omen-arc"):
        raise PermissionError("governed operator: local-only backend")
    if any(args.get(key) is not None for key in ("receipt_dir", "submit_fn", "status_fn", "harvest_fn", "list_files_fn")):
        raise PermissionError("governed operator: internal storage/test overrides denied")
    if name == "create_build_request":
        root = os.environ.get("HERMES_BUILD_REPO")
        if not root or Path(args.get("repo", "")).resolve() != Path(root).resolve():
            raise PermissionError("governed operator: explicit approved build repo required")
        if args.get("execute"):
            raise PermissionError("create a receipt before dispatch")
    if name in {"execute_build_request", "submit_task"}:
        if name == "execute_build_request" and args.get("mode") != "delegate":
            raise PermissionError("controller records delegated work, not agent self-execution")
        if args.get("builders") != ["cc-builder-2", "cc-builder-3"]:
            raise PermissionError("exact qualified local builder pair required; no auto-padding")
        # Admission is opt-in only after current runner routes have been verified.
        # This is a deployment gate, not a claim that a historical catalog is live.
        if os.environ.get("HERMES_LOCAL_BUILDERS_QUALIFIED") != "1":
            raise PermissionError("local builder routes have not been qualified for this deployment")
        age = args.get("max_age_s")
        if type(age) is not int or not 60 <= age <= 2700:
            raise PermissionError("delegated lifetime must be 60..2700 seconds")

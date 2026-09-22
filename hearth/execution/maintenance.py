"""Production startup exclusion; experiment restoration is an explicit exception."""
from __future__ import annotations
import json
from pathlib import Path
import time


def startup_allowed(sentinel: Path, permit: Path, owner, *, now=None):
    if not sentinel.exists():
        return owner is None or (owner.owner == "imagegen" and owner.state == "restoring_llm")
    if owner is None or owner.owner != "experiment" or owner.state != "restoring_llm":
        return False
    try:
        proof = json.loads(permit.read_text(encoding="utf-8"))
        return (proof["session_id"] == owner.session_id and proof["epoch"] == owner.epoch
                and (time.time() if now is None else now) < proof["expires_at"])
    except (OSError, KeyError, ValueError, TypeError):
        return False


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from hearth.execution.coordination import GpuTenancyStore
    root = Path(__file__).resolve().parents[1] / "var"
    owner = None
    try:
        owner = GpuTenancyStore().active_owner()
        if "--stop-check" in sys.argv:
            allowed = owner is None or owner.owner != "experiment" or owner.state == "draining_llm"
        else:
            allowed = startup_allowed(root / "arc-maintenance.stop", root / "arc-experiment-restore.json", owner)
    except Exception:
        allowed = False
    # Scheduled-task diagnostics contain identity/state only, never credentials.
    import os
    with (root / "arc-maintenance-guard.ndjson").open("a", encoding="utf-8") as audit:
        audit.write(json.dumps({"at": time.time(), "pid": os.getpid(), "parent_pid": os.getppid(),
            "mode": "stop" if "--stop-check" in sys.argv else "startup", "allowed": allowed,
            "sentinel": (root / "arc-maintenance.stop").exists(),
            "owner": owner.to_dict() if owner else None}) + "\n")
    if not allowed:
        print("ArcServe startup held by maintenance or GPU ownership")
    raise SystemExit(0 if allowed else 1)

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from hearth.toolsurface.backends import Tool, ToolResult

# Existing tools (for reference)
# submit_task, task_status, etc.

# New tool: task_status with out_file parameter

class TaskStatusArgs(BaseModel):
    plan_id: str = Field(..., description="The plan ID to check")
    out_file: Optional[str] = Field(None, description="Optional path to write full result text to, instead of returning it inline")


class TaskStatusResult(ToolResult):
    path: Optional[str] = None
    bytes: Optional[int] = None
    done: bool = False
    ok: bool = True
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "bytes": self.bytes,
            "done": self.done,
            "ok": self.ok,
            "error": self.error
        }


def task_status(args: TaskStatusArgs) -> TaskStatusResult:
    """Check status of a task and optionally write full result to out_file."""
    import os
    import json
    import subprocess
    from pathlib import Path

    plan_id = args.plan_id
    out_file = args.out_file

    # Check if run exists
    run_dir = Path(f"runs/{plan_id}")
    if not run_dir.exists():
        return TaskStatusResult(ok=False, error=f"Run {plan_id} not found")

    # Check result.json
    result_path = run_dir / "result.json"
    if not result_path.exists():
        return TaskStatusResult(ok=True, done=False)

    # Read result.json
    try:
        with open(result_path, 'r') as f:
            result = json.load(f)
    except Exception as e:
        return TaskStatusResult(ok=False, error=f"Failed to read result.json: {str(e)}")

    # Check if task is done
    if result.get("done") is False:
        return TaskStatusResult(ok=True, done=False)

    # Task is done, write result to out_file if specified
    if out_file:
        try:
            # Ensure out_file is within sandbox
            sandbox_root = Path(".")
            out_path = sandbox_root / out_file
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, 'w') as f:
                f.write(result.get("text", ""))
            return TaskStatusResult(
                path=str(out_file),
                bytes=len(result.get("text", "")),
                done=True,
                ok=True
            )
        except Exception as e:
            return TaskStatusResult(ok=False, error=f"Failed to write to {out_file}: {str(e)}")

    # Return small ack if no out_file
    return TaskStatusResult(done=True, ok=True)

# New tool: queue_status

class QueueStatusArgs(BaseModel):
    pass


class QueueStatusResult(ToolResult):
    queued: int = 0
    running: int = 0
    done: int = 0
    ok: bool = True
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "queued": self.queued,
            "running": self.running,
            "done": self.done,
            "ok": self.ok,
            "error": self.error
        }


def queue_status(args: QueueStatusArgs) -> QueueStatusResult:
    """Report counts of queued, running, and done tasks on the conductor."""
    import os
    import json
    from pathlib import Path

    queued = 0
    running = 0
    done = 0

    inbox_dir = Path("inbox")
    runs_dir = Path("runs")

    # Count queued (inbox files)
    for file in inbox_dir.iterdir():
        if file.is_file() and file.suffix == ".md":
            queued += 1

    # Count running and done (runs/ subdirs)
    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        result_path = run_dir / "result.json"
        if not result_path.exists():
            running += 1
        else:
            try:
                with open(result_path, 'r') as f:
                    result = json.load(f)
                if result.get("done") is True:
                    done += 1
                else:
                    running += 1
            except Exception:
                running += 1

    return QueueStatusResult(queued=queued, running=running, done=done)

# New tool: submit_batch

class SubmitBatchArgs(BaseModel):
    manifest: List[Dict[str, Any]] = Field(..., description="List of task manifests, each with prompt, builders, task_class, plan_id_hint")


class SubmitBatchResult(ToolResult):
    plan_ids: List[str] = Field(default_factory=list)
    ok: bool = True
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_ids": self.plan_ids,
            "ok": self.ok,
            "error": self.error
        }


def submit_batch(args: SubmitBatchArgs) -> SubmitBatchResult:
    """Submit a batch of tasks via existing submit_task mechanism."""
    import os
    import json
    from pathlib import Path
    from hearth.toolsurface.inference import submit_task

    plan_ids = []

    for i, task in enumerate(args.manifest):
        # Extract fields
        prompt = task.get("prompt")
        builders = task.get("builders")
        task_class = task.get("task_class")
        plan_id_hint = task.get("plan_id_hint")

        # Validate required fields
        if not prompt:
            return SubmitBatchResult(ok=False, error=f"Missing prompt in task {i}")

        # Prepare args for submit_task
        submit_args = {
            "prompt": prompt,
            "builders": builders,
            "task_class": task_class,
            "plan_id_hint": plan_id_hint
        }

        # Call submit_task
        try:
            result = submit_task(submit_args)
            if not result.ok:
                return SubmitBatchResult(ok=False, error=f"Failed to submit task {i}: {result.error}")
            plan_ids.append(result.plan_id)
        except Exception as e:
            return SubmitBatchResult(ok=False, error=f"Exception submitting task {i}: {str(e)}")

    return SubmitBatchResult(plan_ids=plan_ids)

# Register tools
# This is done in doorcheck.py, so we just expose them here
TOOL_REGISTRY = {
    "task_status": task_status,
    "queue_status": queue_status,
    "submit_batch": submit_batch
}

# Export for doorcheck.py
__all__ = ["task_status", "queue_status", "submit_batch"]
#!/usr/bin/env python3
"""deepagents_linux — run one bounded DeepAgents delivery from a brief, then account it.

Wraps what already exists and adds nothing to it:
  ~/work/deepagents-linux/run_linux_delivery.py   (frozen input, bounded /output, delivery gates)
  ~/.local/bin/deepagents-account-run <run> --import   (idempotent import into the HEARTH ledger)

Spec (written by fleet.bankedfire_linux from a `task_class: deepagents` brief):
  {"id", "source": <one file the agent may read/edit>, "task": <prose>,
   "backend": omen|am4|omen-dense|am4-tool-4070ti|am4-tool-5070,
   "report": bool, "max_report_words": int|null, "work": <ct work id>|null}

Outcome: "succeeded" when the runner exits 0 AND result.json says accepted_shape (the candidate
still carries review_required=true — a human or frontier caller reviews it; unattended DeepAgents
delivery stays unqualified per research/deepagents.md §5); otherwise "failed". The accounting
import runs either way so every physical attempt lands on the ledger. State is persisted per
phase under $HEARTH_ROOT/var/experiments/deepagents/<id>/state.json.

    python -m fleet.deepagents_linux run --spec <spec.json>
    python -m fleet.deepagents_linux status --id <id>
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

HEARTH_ROOT = Path(os.environ.get("HEARTH_ROOT", str(Path.home() / "hearth-production")))
STATE_ROOT = HEARTH_ROOT / "var" / "experiments" / "deepagents"
DA_ROOT = Path(os.environ.get("DEEPAGENTS_LINUX", str(Path.home() / "work" / "deepagents-linux")))
DA_PYTHON = Path(os.environ.get("DEEPAGENTS_PYTHON", str(Path.home() / ".venvs" / "deepagents-linux" / "bin" / "python")))
ACCOUNT = Path.home() / ".local" / "bin" / "deepagents-account-run"
RUN_TIMEOUT_S = 2100  # the runner's own deadline is 1800 s (run_linux_delivery.DEADLINE_S) + 300 (sizing-map 2026-09-28)
POOL = "omen-b70-pool"
AM4_ROUTES = ("am4", "am4-tool-4070ti", "am4-tool-5070")
AM4_PROFILE_OF = {"am4-tool-4070ti": "tool-pair", "am4-tool-5070": "tool-pair", "am4": "dense-tp2"}
AM4_SSH = "10.44.0.2"   # direct cable (ADR-0014: never the tailnet)


def utc() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


class Delivery:
    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.id = str(spec["id"])
        self.dir = STATE_ROOT / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.state: dict[str, Any] = {"schema": "deepagents-linux-run.v1", "id": self.id, "spec": spec,
                                      "phase": "created", "started": utc(), "outcome": None}

    def save(self, phase: Optional[str] = None, **extra: Any) -> None:
        if phase:
            self.state["phase"] = phase
        self.state.update(extra); self.state["updated"] = utc()
        tmp = self.state_path.with_suffix(".tmp"); tmp.write_text(json.dumps(self.state, indent=2, default=str)); os.replace(tmp, self.state_path)

    def run(self) -> int:
        # 2026-09-27 20:56Z: a delivery launched while the seat experiment had the pool died in 2 s
        # ("rendered prompt check failed: 500"). The fence is the truth about who owns the seats.
        backend = str(self.spec.get("backend", "omen-dense"))
        if backend in AM4_ROUTES:
            # AM4 routes are fenced by the AM4 profile, not the OMEN B70 pool: refuse unless the
            # profile that serves this alias is the live one (tool-pair for the per-card seats).
            live = am4_profile()
            if live != AM4_PROFILE_OF.get(backend):
                self.save("done", outcome="failed", finished=utc(), review_required=True,
                          fence=f"am4 profile is {live or 'unreadable'}; {backend} needs {AM4_PROFILE_OF.get(backend)}; not launching")
                print(json.dumps({"id": self.id, "outcome": "failed", "fence": f"am4-profile:{live}"}), flush=True)
                return 1
        else:
            try:
                from hearth.execution.coordination import GpuTenancyStore, default_coordination_path
                fence_db = default_coordination_path()
                if not fence_db.is_file():
                    # GpuTenancyStore would create an empty DB here and report "no owner": a missing
                    # fence means we are looking in the wrong place, not that the pool is free.
                    raise FileNotFoundError(f"fence db missing: {fence_db}")
                owner = GpuTenancyStore(fence_db).active_owner(POOL)
            except Exception as exc:  # noqa: BLE001 -- an unreadable fence is not a reason to spend
                # Fail closed: not knowing who owns the pool is not permission to launch on it.
                self.save("done", outcome="failed", finished=utc(), review_required=True, fence="unreadable",
                          fence_error=f"{type(exc).__name__}: {exc}")
                print(json.dumps({"id": self.id, "outcome": "failed", "fence": "unreadable"}), flush=True)
                return 1
            if owner is not None:
                self.save("done", outcome="failed", finished=utc(), review_required=True,
                          fence=f"pool owned by {owner.owner} session {owner.session_id}; not launching")
                print(json.dumps({"id": self.id, "outcome": "failed", "fence": owner.owner}), flush=True)
                return 1
        run_dir = DA_ROOT / "runs" / self.id
        task_file = self.dir / "task.txt"
        task_file.write_text(str(self.spec["task"]).strip() + "\n")
        argv = [str(DA_PYTHON), str(DA_ROOT / "run_linux_delivery.py"), "--source", str(self.spec["source"]),
                "--destination", str(run_dir), "--task-file", str(task_file), "--backend", str(self.spec.get("backend", "omen-dense"))]
        if self.spec.get("report"):
            argv.append("--report")
            if self.spec.get("max_report_words"):
                argv += ["--max-report-words", str(int(self.spec["max_report_words"]))]
        work = self.spec.get("work")
        if work and shutil.which("ct"):
            argv = ["ct", "receipt", "--work", str(work), "--"] + argv
        self.save("running", argv=argv, run_dir=str(run_dir))
        with (self.dir / "runner.out").open("ab") as out:
            try:
                proc = subprocess.run(argv, stdout=out, stderr=subprocess.STDOUT, cwd=str(DA_ROOT), timeout=RUN_TIMEOUT_S, check=False)
                rc: Optional[int] = proc.returncode
            except subprocess.TimeoutExpired:
                rc = None
        result = {}
        try:
            result = json.loads((run_dir / "result.json").read_text())
        except Exception:  # noqa: BLE001
            pass
        self.save("delivered", runner_rc=rc, result=result)
        # accounting: every physical attempt lands on the ledger, success or not
        acct_rc: Optional[int] = None
        if ACCOUNT.exists() and (run_dir / "outbox.sqlite").exists():
            with (self.dir / "accounting.out").open("ab") as out:
                try:
                    acct_rc = subprocess.run([sys.executable, str(ACCOUNT), self.id, "--import"], stdout=out,
                                             stderr=subprocess.STDOUT, timeout=300, check=False).returncode
                except subprocess.TimeoutExpired:
                    acct_rc = None
        outcome = "succeeded" if rc == 0 and result.get("accepted_shape") else "failed"
        self.save("done", accounting_rc=acct_rc, outcome=outcome, finished=utc(),
                  candidate=str(run_dir / "agent-fs" / "output"), review_required=True)
        print(json.dumps({"id": self.id, "outcome": outcome, "runner_rc": rc, "accounting_rc": acct_rc}), flush=True)
        return 0 if outcome == "succeeded" else 1


def am4_profile(timeout_s: int = 6) -> Optional[str]:
    """The profile name AM4 says is live (~/.config/am4-fleet/profile, written by am4-profile), or None."""
    try:
        out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout_s}", AM4_SSH,
                              "cat ~/.config/am4-fleet/profile"], capture_output=True, text=True, timeout=timeout_s + 4)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def status(run_id: str) -> dict[str, Any]:
    p = STATE_ROOT / run_id / "state.json"
    if not p.exists():
        return {"id": run_id, "phase": "missing"}
    d = json.loads(p.read_text())
    return {k: d.get(k) for k in ("id", "phase", "outcome", "started", "updated", "runner_rc", "accounting_rc", "result", "candidate")}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--spec", required=True)
    s = sub.add_parser("status"); s.add_argument("--id", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "status":
        print(json.dumps(status(args.id), indent=2, default=str)); return 0
    return Delivery(json.loads(Path(args.spec).read_text())).run()


if __name__ == "__main__":
    raise SystemExit(main())

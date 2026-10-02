#!/usr/bin/env python3
"""experiment_linux — one seat experiment on omen-linux, fenced, restored, proven.

The Linux port of deepagents-poc/poc/experiment_window.py's ceremony, for vLLM seats:

  acquire   GpuTenancyStore.acquire(resource="omen-b70-pool", owner="experiment")
            -> the door's omen-vllm/omen-arc probes read "busy exclusive", so no opportunistic
               local_generate and no drain dispatch lands while the pool is ours.
  snapshot  the seat's active drop-ins, served model ids, unit state (the thing to restore to)
  swap      copy <dropin>.conf.staged -> <dropin>.conf in omen-vllm@<seat>.service.d,
            daemon-reload, restart the seat, ~/bin/wait-vllm-seat.sh (fails within ~3 s of a
            crash loop), assert the served model is the one the brief expects
  campaign  run the brief's command with a hard timeout = min(max_minutes, minutes until
            restore_by - margin); under `ct receipt --work <id>` when a work item is named
  restore   remove the drop-in, daemon-reload, restart, wait, assert served == snapshot
  release   GpuTenancyStore.release(owner="experiment", restoration_verified=True)

Persist-first: state.json under $HEARTH_ROOT/var/experiments/linux/<id>/ is rewritten at every
phase, so a crash leaves a readable phase and a held fence, never a silently swapped seat.
A failed restore keeps the tenancy (fail closed) and exits 2: the pool stays fenced until a
human looks. Stdlib only; runs detached under a transient systemd unit started by the drain.

    python -m fleet.experiment_linux run --spec <spec.json>
    python -m fleet.experiment_linux status --id <id>
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

HEARTH_ROOT = Path(os.environ.get("HEARTH_ROOT", str(Path.home() / "hearth-production")))
EXP_ROOT = HEARTH_ROOT / "var" / "experiments" / "linux"
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
WAIT_SCRIPT = Path.home() / "bin" / "wait-vllm-seat.sh"
POOL = "omen-b70-pool"
RESTORE_MARGIN_MIN = 20
DEFAULT_RESTORE_BY = "06:30"
DEFAULT_MAX_MINUTES = 240


def utc() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"



def _environment_stamp() -> dict[str, Any]:
    """ADR-0053: the policy environment this experiment started under."""
    try:
        from fleet import environment as envmod
        return envmod.stamp()
    except Exception as exc:  # noqa: BLE001 -- a stamp never blocks a run; its absence is recorded
        return {"name": "unreadable", "error": type(exc).__name__}

class Experiment:
    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.id = str(spec["id"])
        self.seat = int(spec["seat"])
        self.dropin = str(spec["dropin"])
        if not self.dropin.endswith(".conf"):
            self.dropin += ".conf"
        self.dir = EXP_ROOT / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.state: dict[str, Any] = {"schema": "experiment-linux.v1", "id": self.id, "spec": spec,
                                      "phase": "created", "started": utc(), "outcome": None, "log": [],
                                      "environment": _environment_stamp()}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
        self.tenancy = None

    # --- persistence -------------------------------------------------------------------
    def save(self, phase: Optional[str] = None, **extra: Any) -> None:
        if phase:
            self.state["phase"] = phase
        self.state.update(extra)
        self.state["updated"] = utc()
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2, default=str))
        os.replace(tmp, self.state_path)

    def log(self, msg: str, **fields: Any) -> None:
        row = {"at": utc(), "msg": msg, **fields}
        self.state.setdefault("log", []).append(row)
        with (self.dir / "log.ndjson").open("a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        print(f"[experiment {self.id}] {msg} {json.dumps(fields, default=str) if fields else ''}", flush=True)

    # --- seat helpers ------------------------------------------------------------------
    @property
    def unit(self) -> str:
        return f"omen-vllm@{self.seat}.service"

    @property
    def service_d(self) -> Path:
        return UNIT_DIR / f"omen-vllm@{self.seat}.service.d"

    @property
    def port(self) -> int:
        return 18091 + self.seat

    def _vllm_key(self) -> str:
        key = os.environ.get("VLLM_API_KEY") or os.environ.get("OMEN_ARC_TOKEN")
        if not key:
            env = Path.home() / ".config/omen-vllm/omen-api.env"
            for line in env.read_text().splitlines():
                if line.startswith("VLLM_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"')
        return key or ""

    def served_models(self) -> list[str]:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/models",
                                     headers={"Authorization": f"Bearer {self._vllm_key()}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return sorted(m["id"] for m in data.get("data", []))

    def active_dropins(self) -> list[str]:
        return sorted(p.name for p in self.service_d.glob("*.conf"))

    def snapshot(self) -> dict[str, Any]:
        return {"dropins": self.active_dropins(), "served": self.served_models(),
                "active": subprocess.run(["systemctl", "--user", "is-active", self.unit],
                                         capture_output=True, text=True).stdout.strip()}

    def restart_and_wait(self) -> None:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "restart", self.unit], check=True)
        out = subprocess.run([str(WAIT_SCRIPT), str(self.seat), "900"], capture_output=True, text=True)
        self.log("wait-vllm-seat", rc=out.returncode, tail=(out.stdout + out.stderr)[-400:])
        if out.returncode != 0:
            raise RuntimeError(f"seat {self.seat} did not come up: {(out.stdout + out.stderr)[-300:]}")

    # --- phases ------------------------------------------------------------------------
    def acquire(self) -> None:
        from hearth.execution.coordination import GpuTenancyStore
        store = GpuTenancyStore()
        owner = store.active_owner(POOL)
        if owner is not None:
            raise RuntimeError(f"pool owned by {owner.owner} session {owner.session_id}; refusing")
        max_minutes = int(self.spec.get("max_minutes", DEFAULT_MAX_MINUTES))
        snap = store.acquire(resource=POOL, session_id=self.id, ttl_seconds=max(3600, max_minutes * 60 + 1800),
                             state="draining_llm", reason=f"experiment {self.id}: {self.dropin} on seat {self.seat}",
                             owner="experiment")
        self.tenancy = {"epoch": snap.epoch, "session_id": snap.session_id}
        self.save("acquired", tenancy=self.tenancy)
        self.log("tenancy acquired", epoch=snap.epoch)

    def take_snapshot(self) -> None:
        snap = self.snapshot()
        self.save("snapshot", resident=snap)
        self.log("resident snapshot", **snap)

    def swap(self) -> None:
        staged = self.service_d / (self.dropin + ".staged")
        target = self.service_d / self.dropin
        if not staged.exists():
            raise RuntimeError(f"no staged drop-in {staged}")
        shutil.copy2(staged, target)
        self.save("swapped", applied_dropin=str(target))
        self.log("drop-in applied", dropin=self.dropin)
        self.restart_and_wait()
        served = self.served_models()
        expect = self.spec.get("expect_model")
        if expect and expect not in served:
            raise RuntimeError(f"seat serves {served}, expected {expect}")
        self.save("swapped-verified", served_after_swap=served)
        self.log("swap verified", served=served)

    def campaign_budget_s(self) -> int:
        max_minutes = int(self.spec.get("max_minutes", DEFAULT_MAX_MINUTES))
        hhmm = str(self.spec.get("restore_by", DEFAULT_RESTORE_BY))
        now = datetime.now()
        h, m = (int(x) for x in hhmm.split(":"))
        restore_by = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if restore_by <= now:
            restore_by += timedelta(days=1)
        until = (restore_by - now).total_seconds() - RESTORE_MARGIN_MIN * 60
        return int(max(60, min(max_minutes * 60, until)))

    def campaign(self) -> None:
        cmd = self.spec["campaign"]
        argv = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
        work = self.spec.get("work")
        if work and shutil.which("ct"):
            argv = ["ct", "receipt", "--work", str(work), "--"] + argv
        budget = self.campaign_budget_s()
        self.save("campaign_running", campaign_argv=argv, campaign_budget_s=budget, campaign_started=utc())
        self.log("campaign start", budget_s=budget)
        with (self.dir / "campaign.out").open("ab") as out:
            proc = subprocess.run(argv, stdout=out, stderr=subprocess.STDOUT, timeout=budget,
                                  cwd=str(Path.home()), check=False)
        self.save("campaign_done", campaign_rc=proc.returncode, campaign_finished=utc())
        self.log("campaign done", rc=proc.returncode)

    def restore(self) -> None:
        target = self.service_d / self.dropin
        if target.exists():
            target.unlink()
        self.save("restoring", applied_dropin=None)
        self.log("drop-in removed")
        self.restart_and_wait()
        served = self.served_models()
        want = (self.state.get("resident") or {}).get("served")
        dropins = self.active_dropins()
        want_dropins = (self.state.get("resident") or {}).get("dropins")
        if served != want or dropins != want_dropins:
            raise RuntimeError(f"restore mismatch: served {served} vs {want}; dropins {dropins} vs {want_dropins}")
        self.save("restored", served_after_restore=served)
        self.log("restore verified", served=served)

    def release(self) -> None:
        from hearth.execution.coordination import GpuTenancyStore
        t = self.state.get("tenancy") or {}
        GpuTenancyStore().release(resource=POOL, session_id=t["session_id"], epoch=int(t["epoch"]),
                                  owner="experiment", restoration_verified=True,
                                  reason=f"experiment {self.id} restored and verified")
        self.save("released")
        self.log("tenancy released")

    # --- the run -----------------------------------------------------------------------
    def run(self) -> int:
        outcome = "failed"
        try:
            self.acquire()
            self.take_snapshot()
            self.swap()
            self.campaign()
            outcome = "succeeded" if self.state.get("campaign_rc") == 0 else "failed"
        except subprocess.TimeoutExpired:
            self.log("campaign timed out"); outcome = "failed"
            self.save("campaign_done", campaign_rc=None, campaign_timed_out=True)
        except Exception as exc:  # noqa: BLE001 -- everything below still restores
            self.log("phase failed", error=f"{type(exc).__name__}: {exc}")
            outcome = "failed"
            if self.state.get("phase") in ("created", "acquired"):
                # nothing was swapped; release the fence if we hold it and stop
                if self.state.get("tenancy"):
                    try:
                        self.save("restored"); self.release()
                    except Exception as rexc:  # noqa: BLE001
                        self.log("release failed", error=str(rexc))
                self.save("done", outcome=outcome)
                return 1
        # restore always runs once a swap was attempted
        try:
            for attempt in (1, 2):
                try:
                    self.restore(); break
                except Exception as exc:  # noqa: BLE001
                    self.log("restore attempt failed", attempt=attempt, error=f"{type(exc).__name__}: {exc}")
                    if attempt == 2:
                        self.save("done", outcome="restore_failed")
                        self.log("RESTORE FAILED — tenancy kept, pool fenced, human needed")
                        return 2
            self.release()
        except Exception as exc:  # noqa: BLE001
            self.log("release failed", error=f"{type(exc).__name__}: {exc}")
            self.save("done", outcome="restore_failed")
            return 2
        self.save("done", outcome=outcome, finished=utc())
        self.log("done", outcome=outcome)
        return 0 if outcome == "succeeded" else 1


def status(exp_id: str) -> dict[str, Any]:
    p = EXP_ROOT / exp_id / "state.json"
    if not p.exists():
        return {"id": exp_id, "phase": "missing"}
    d = json.loads(p.read_text())
    return {k: d.get(k) for k in ("id", "phase", "outcome", "started", "updated", "campaign_rc", "resident", "served_after_swap")}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--spec", required=True)
    s = sub.add_parser("status"); s.add_argument("--id", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "status":
        print(json.dumps(status(args.id), indent=2, default=str)); return 0
    spec = json.loads(Path(args.spec).read_text())
    return Experiment(spec).run()


if __name__ == "__main__":
    raise SystemExit(main())

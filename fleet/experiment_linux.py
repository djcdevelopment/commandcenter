#!/usr/bin/env python3
"""experiment_linux — one seat experiment on omen-linux, fenced, restored, proven.

The Linux port of deepagents-poc/poc/experiment_window.py's ceremony, for vLLM seats:

  acquire   GpuTenancyStore.acquire(resource="omen-b70-pool", owner="experiment")
            -> the door's omen-vllm/omen-arc probes read "busy exclusive", so no opportunistic
               local_generate and no drain dispatch lands while the pool is ours.
  preflight refuse a busy seat (/metrics running/waiting), an active door lease on the seat's
            backend, a resident file at the drop-in's name, a missing staged file
  snapshot  the seat's active drop-ins, served model ids, unit state, and sha256 of every
            drop-in *.conf, the launcher, haproxy.cfg, the model's config/tokenizer/template
            files (weights: names and sizes) -- the thing to restore to
  swap      (spec "dropin": null = baseline arm: no swap, no restart) copy <dropin>.staged ->
            <dropin> in omen-vllm@<seat>.service.d, daemon-reload, dry-run the launcher
            (OMEN_DRY=1) with the unit's Environment= to get the effective argv, compare with
            spec "expect_argv" BEFORE any restart, restart the seat, ~/bin/wait-vllm-seat.sh,
            check the running process's command line equals that argv, assert the served model
  campaign  run the brief's command with a hard timeout = min(max_minutes, minutes until
            restore_by - margin); under `ct receipt --work <id>` when a work item is named;
            the tenancy is renewed meanwhile; seat request_success_total before/after minus the
            campaign's last-line {"calls": N} = foreign_requests (nonzero -> outcome "void");
            on timeout the spec's optional "on_timeout" command runs before restore
  restore   remove the drop-in, daemon-reload, restart (only if the seat was restarted), wait,
            assert served and every hash == snapshot
  release   GpuTenancyStore.release(owner="experiment", restoration_verified=True)

Persist-first: state.json under $HEARTH_ROOT/var/experiments/linux/<id>/ is rewritten at every
phase, so a crash leaves a readable phase and a held fence, never a silently swapped seat.
A failed restore keeps the tenancy (fail closed) and exits 2: the pool stays fenced until a
human looks. SIGTERM/^C before restore takes the restore path (the campaign's process group gets
SIGTERM first); restore itself ignores both. An id whose state.json exists is refused (use a new id).
Stdlib only; runs detached under a transient systemd unit started by the drain.

    python -m fleet.experiment_linux run --spec <spec.json>
    python -m fleet.experiment_linux status --id <id>
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import hashlib
import re
import signal
import subprocess
import sys
import threading
import tomllib
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
# the door's fence and lease store (hearth-production.service sets the same path); hearth.execution.coordination
# would otherwise default to <this checkout>/hearth/var/execution, a file the door never reads
COORD_DB = Path(os.environ.get("HEARTH_COORDINATION_DB", str(HEARTH_ROOT / "var" / "execution" / "coordination.sqlite")))
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
WAIT_SCRIPT = Path.home() / "bin" / "wait-vllm-seat.sh"
POOL = "omen-b70-pool"
RESTORE_MARGIN_MIN = 20
DEFAULT_RESTORE_BY = "06:30"
DEFAULT_MAX_MINUTES = 240
LAUNCHER = Path.home() / "bin" / "start-vllm-seat.sh"
HAPROXY_CFG = Path.home() / ".config" / "omen-vllm" / "haproxy.cfg"
DEFAULT_MODEL = "/home/derek/models/qwen3-30b-a3b-gptq-int4"   # the launcher's OMEN_MODEL default
SEAT_BACKEND = {0: "omen-dense-27b", 1: "omen-vllm"}   # lease scope provider:<backend>; spec "backend" overrides
MODEL_SMALL_FILES = ("config.json", "generation_config.json", "tokenizer*", "chat_template*", "vocab*", "merges.txt",
                     "special_tokens_map.json", "added_tokens.json", "preprocessor_config.json", "*.index.json")
TENANCY_TTL_S = 600
TENANCY_RENEW_S = 60
CAMPAIGN_KILL_GRACE_S = 30


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
        self.dropin: Optional[str] = None   # None = baseline arm
        if spec["dropin"] is not None:
            self.dropin = str(spec["dropin"])
            if not self.dropin.endswith(".conf"):
                self.dropin += ".conf"
        self.dir = EXP_ROOT / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.reused = self.state_path.exists()   # run() refuses: an old state would steer restore
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

    def unit_env(self) -> dict[str, str]:
        out = subprocess.run(["systemctl", "--user", "show", self.unit, "-p", "Environment", "--value"],
                             capture_output=True, text=True, check=True).stdout.strip()
        return dict(tok.split("=", 1) for tok in shlex.split(out))

    @staticmethod
    def _sha(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    def stack_hashes(self, model_dir: str) -> dict[str, Any]:
        md = Path(model_dir)
        paths = sorted(self.service_d.glob("*.conf")) + [LAUNCHER, HAPROXY_CFG]
        for pat in MODEL_SMALL_FILES:
            paths += sorted(md.glob(pat))
        return {"model_dir": model_dir, "hashes": {str(p): self._sha(p) for p in dict.fromkeys(paths)},
                "weights": {p.name: p.stat().st_size for p in sorted(md.glob("*.safetensors"))}}

    def main_proc(self) -> tuple[str, list[str]]:
        pid = subprocess.run(["systemctl", "--user", "show", self.unit, "-p", "MainPID", "--value"],
                             capture_output=True, text=True, check=True).stdout.strip()
        return pid, Path(f"/proc/{pid}/cmdline").read_text().split("\0")[:-1]

    def snapshot(self) -> dict[str, Any]:
        model_dir = self.unit_env().get("OMEN_MODEL", DEFAULT_MODEL)
        pid, cmd = self.main_proc()
        return {"dropins": self.active_dropins(), "served": self.served_models(), "main_pid": pid, "running_argv": cmd,
                "active": subprocess.run(["systemctl", "--user", "is-active", self.unit],
                                         capture_output=True, text=True).stdout.strip(),
                **self.stack_hashes(model_dir)}

    def effective_argv(self) -> list[str]:
        """The vllm argv the launcher would build from the unit's current Environment= (OMEN_DRY=1, no start)."""
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(Path.home()), **self.unit_env(), "OMEN_DRY": "1"}
        out = subprocess.run([str(LAUNCHER), str(self.seat)], env=env, capture_output=True, text=True)
        if out.returncode != 0:
            raise RuntimeError(f"launcher dry run rc={out.returncode}: {out.stderr[-300:]}")
        m = re.search(r"^vllm=(\S+)", LAUNCHER.read_text(), re.M)
        if not m:
            raise RuntimeError("launcher names no vllm binary")
        return [m.group(1)] + out.stdout.splitlines()

    def check_running_args(self, argv: list[str]) -> None:
        pid, cmd = self.main_proc()
        if "serve" not in cmd or cmd[cmd.index("serve") - 1:] != argv:
            raise RuntimeError(f"running process (pid {pid}) does not carry the effective argv")
        self.log("running process matches effective argv", pid=pid)

    def record_argv(self, running_must_match: bool) -> list[str]:
        argv = self.effective_argv()
        expect = self.spec.get("expect_argv")
        self.save(effective_argv=argv)
        self.log("effective argv", n=len(argv))
        if expect is not None and list(expect) != argv:
            diff = [(i, a, b) for i, (a, b) in enumerate(zip(argv, expect)) if a != b][:3]
            raise RuntimeError(f"effective argv != expect_argv (lengths {len(argv)}/{len(expect)}; first diffs {diff})")
        if running_must_match:
            self.check_running_args(argv)
        return argv

    # --- seat metrics, ledger ----------------------------------------------------------
    def seat_counters(self) -> dict[str, float]:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/metrics",
                                     headers={"Authorization": f"Bearer {self._vllm_key()}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read().decode("utf-8", "replace")
        want = {"vllm:num_requests_running": "running", "vllm:num_requests_waiting": "waiting",
                "vllm:request_success_total": "success"}
        found: dict[str, float] = {}
        for line in text.splitlines():
            name = line.split("{", 1)[0].split(" ", 1)[0]
            if name in want:
                found[want[name]] = found.get(want[name], 0.0) + float(line.rsplit(" ", 1)[1])
        if len(found) != len(want):
            raise RuntimeError(f"seat {self.seat} /metrics lacks {sorted(set(want.values()) - set(found))}")
        return found

    def refuse_busy(self) -> dict[str, float]:
        c = self.seat_counters()
        if c["running"] or c["waiting"]:
            raise RuntimeError(f"seat {self.seat} is busy: running {c['running']:g} waiting {c['waiting']:g}")
        return c

    def preflight(self) -> None:
        if self.dropin:
            if (self.service_d / self.dropin).exists():
                raise RuntimeError(f"resident file {self.service_d / self.dropin} already exists; refusing to overwrite it")
            if not (self.service_d / (self.dropin + ".staged")).exists():
                raise RuntimeError(f"no staged drop-in {self.service_d / (self.dropin + '.staged')}")
        c = self.refuse_busy()
        from hearth.execution.coordination import CapacityLeaseStore
        backend = str(self.spec.get("backend") or SEAT_BACKEND[self.seat])
        n = CapacityLeaseStore(COORD_DB).active_count(f"provider:{backend}")
        if n:
            raise RuntimeError(f"{n} active door lease(s) on provider:{backend}; refusing")
        self.log("preflight clear", backend=backend, counters=c)

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
        store = GpuTenancyStore(COORD_DB)
        owner = store.active_owner(POOL)
        if owner is not None:
            raise RuntimeError(f"pool owned by {owner.owner} session {owner.session_id}; refusing")
        snap = store.acquire(resource=POOL, session_id=self.id, ttl_seconds=TENANCY_TTL_S,
                             state="draining_llm", reason=f"experiment {self.id}: " + ("paired seats 0 and 1" if "seats" in self.spec else f"{self.dropin or 'baseline'} on seat {self.seat}"),
                             owner="experiment")
        self.tenancy = {"epoch": snap.epoch, "session_id": snap.session_id}
        self.save("acquired", tenancy=self.tenancy)
        self.log("tenancy acquired", epoch=snap.epoch)

    def take_snapshot(self) -> None:
        snap = self.snapshot()
        self.save("snapshot", resident=snap)
        self.log("resident snapshot", **snap)

    def swap(self) -> None:
        if not self.dropin:
            self.record_argv(running_must_match=True)   # baseline: nothing changes, the running seat must be what the files say
            self.save("swapped-verified", served_after_swap=self.served_models())
            self.log("baseline arm: no swap, no restart")
            return
        staged = self.service_d / (self.dropin + ".staged")
        target = self.service_d / self.dropin
        if not staged.exists():
            raise RuntimeError(f"no staged drop-in {staged}")
        if target.exists():
            raise RuntimeError(f"resident file {target} appeared after preflight; refusing to overwrite it")
        self.save("swapping", applied_dropin=str(target))   # persisted first: restore removes only what we applied
        shutil.copy2(staged, target)
        self.save("swapped")
        self.log("drop-in applied", dropin=self.dropin)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        argv = self.record_argv(running_must_match=False)   # a mismatch stops here, before any restart
        self.refuse_busy()   # omen-dense-27b is not behind the pool fence: a request since preflight is not ours to kill
        self.save("restarting", seat_restarted=True)
        self.restart_and_wait()
        self.check_running_args(argv)
        self.save(stack_swapped=self.stack_hashes(argv[2]))
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

    def _renew_loop(self, stop: threading.Event, box: dict[str, int]) -> None:
        from hearth.execution.coordination import GpuTenancyStore
        store, t = GpuTenancyStore(COORD_DB), self.state["tenancy"]
        while True:
            try:
                ok = store.renew(resource=POOL, session_id=t["session_id"], epoch=int(t["epoch"]),
                                 ttl_seconds=TENANCY_TTL_S, owner="experiment")
            except Exception as exc:  # noqa: BLE001 -- counted as a failed renewal (outcome void), not dropped
                ok, box["error"] = False, f"{type(exc).__name__}: {exc}"
            box["renewed" if ok else "failed"] += 1
            if stop.wait(TENANCY_RENEW_S):
                return

    def _calls_reported(self, offset: int) -> Optional[int]:
        lines = (self.dir / "campaign.out").read_bytes()[offset:].decode("utf-8", "replace").splitlines()
        for line in reversed([l.strip() for l in lines if l.strip()]):
            if line.startswith("{"):
                try:
                    return int(json.loads(line)["calls"])
                except (ValueError, KeyError, TypeError):
                    continue
        return None

    def foreign_requests(self, before, after, calls):
        return None if calls is None or after is None else int(after["success"] - before["success"]) - calls

    def stop_group(self, proc: subprocess.Popen) -> None:
        """SIGTERM the campaign's process group (the driver cancels its executions), SIGKILL after a grace."""
        for sig, grace in ((signal.SIGTERM, CAMPAIGN_KILL_GRACE_S), (signal.SIGKILL, 10)):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:   # the whole group has already exited
                break
            try:
                proc.wait(timeout=grace)
                break
            except subprocess.TimeoutExpired:
                continue
        self.log("campaign process group stopped", rc=proc.returncode)

    def campaign(self) -> None:
        cmd = self.spec["campaign"]
        argv = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
        work = self.spec.get("work")
        if work and shutil.which("ct"):
            argv = ["ct", "receipt", "--work", str(work), "--"] + argv
        budget = self.campaign_budget_s()
        before = self.seat_counters()
        out_path = self.dir / "campaign.out"
        offset = out_path.stat().st_size if out_path.exists() else 0
        self.save("campaign_running", campaign_argv=argv, campaign_budget_s=budget, campaign_started=utc(),
                  counters_before=before)
        self.log("campaign start", budget_s=budget)
        stop, box = threading.Event(), {"renewed": 0, "failed": 0}
        renewer = threading.Thread(target=self._renew_loop, args=(stop, box), daemon=True)
        renewer.start()
        timed_out, rc = False, None
        try:
            with out_path.open("ab") as out:
                # own process group: a timeout or our own SIGTERM reaches the driver under `ct receipt` too
                proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT, cwd=str(Path.home()),
                                        start_new_session=True)
                try:
                    rc = proc.wait(timeout=budget)
                    self.stop_group(proc)  # normal exit must not leave campaign descendants alive
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self.stop_group(proc)
                except BaseException:
                    self.stop_group(proc)
                    raise
        finally:
            stop.set(); renewer.join(10)
        if timed_out:
            self.log("campaign timed out")
            if self.spec.get("on_timeout"):
                oc = self.spec["on_timeout"]
                oargv = shlex.split(oc) if isinstance(oc, str) else list(oc)
                p = subprocess.run(oargv, capture_output=True, text=True, timeout=120, cwd=str(Path.home()), check=False)
                self.log("on_timeout ran", rc=p.returncode, tail=(p.stdout + p.stderr)[-300:])
                self.save(on_timeout_rc=p.returncode)
        after = self.sample_counters()
        calls = self._calls_reported(offset)
        foreign = self.foreign_requests(before, after, calls)
        self.save("campaign_done", campaign_rc=rc, campaign_timed_out=timed_out, campaign_finished=utc(),
                  counters_after=after, calls_reported=calls, foreign_requests=foreign,
                  tenancy_renewals=box["renewed"], tenancy_renew_failures=box["failed"],
                  tenancy_renew_error=box.get("error"))
        self.log("campaign done", rc=rc, calls=calls, foreign_requests=foreign, renewals=box["renewed"])
        if calls is None:
            self.log("campaign reported no calls line; foreign requests cannot be attributed")

    def sample_counters(self):
        try:
            return self.seat_counters()
        except Exception as exc:
            self.save(counters_sample_error=type(exc).__name__)
            return None

    def wait_drained(self, timeout_s: float = 3600) -> dict[str, float]:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                counters = self.seat_counters()
            except Exception:
                result = subprocess.run(["systemctl", "--user", "show", self.unit, "-p", "ActiveState", "-p", "SubState", "-p", "MainPID"],
                                        check=True, capture_output=True, text=True).stdout
                unit = dict(line.split("=", 1) for line in result.splitlines() if "=" in line)
                from hearth.execution.coordination import CapacityLeaseStore
                backend = str(self.spec.get("backend") or SEAT_BACKEND[self.seat])
                leases = CapacityLeaseStore(COORD_DB).active_count(f"provider:{backend}")
                stopped = unit.get("SubState") in ("auto-restart", "auto-restart-queued", "dead", "failed") and unit.get("MainPID") == "0"
                if stopped and not leases:
                    self.save(counters_unavailable=True, stopped_state_before_restore=unit)
                    return {"running": 0, "waiting": 0, "success": None}
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"seat {self.seat} did not become safely restartable: {unit}, leases={leases}")
                time.sleep(1)
                continue
            if not counters["running"] and not counters["waiting"]:
                return counters
            if time.monotonic() >= deadline:
                raise RuntimeError(f"seat {self.seat} failed to drain within {timeout_s}s")
            time.sleep(1)

    def restore(self) -> None:
        if self.state.get("phase") == "restored":
            return
        if self.dropin:
            target = self.service_d / self.dropin
            if self.state.get("applied_dropin") and target.exists():
                target.unlink()
            self.save("restoring", applied_dropin=None)
            self.log("drop-in removed")
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        if (self.dropin and self.state.get("seat_restarted")) or self.state.get("cold_restart_pending"):
            self.wait_drained()   # only an actual restart needs a drain
            self.restart_and_wait()
            self.save(restoration_restarted=True, cold_restart_pending=False)
        res = self.state.get("resident") or {}
        served = self.served_models()
        dropins = self.active_dropins()
        if served != res.get("served") or dropins != res.get("dropins"):
            raise RuntimeError(f"restore mismatch: served {served} vs {res.get('served')}; dropins {dropins} vs {res.get('dropins')}")
        pid, cmd = self.main_proc()
        if cmd != res["running_argv"]:
            raise RuntimeError(f"restore mismatch: running process (pid {pid}) argv differs from the snapshot's")
        if not self.state.get("seat_restarted") and pid != res["main_pid"]:
            self.save(seat_restarted_externally=pid)   # not restarted by us: the seat was not ours alone
        now = self.stack_hashes(res["model_dir"])
        if now["hashes"] != res["hashes"] or now["weights"] != res["weights"]:
            bad = sorted(k for k in set(now["hashes"]) | set(res["hashes"]) if now["hashes"].get(k) != res["hashes"].get(k))
            raise RuntimeError(f"restore hash mismatch: {bad[:5]}; weights equal: {now['weights'] == res['weights']}")
        self.save("restored", served_after_restore=served, hashes_verified=len(res["hashes"]))
        self.log("restore verified", served=served, hashes=len(res["hashes"]))

    def release(self) -> None:
        if "seats" not in self.spec and self.state.get("resident"):
            counters = self.sample_counters()
            self.save(counters_release=counters)
            baseline = 0 if self.state.get("restoration_restarted") else (self.state.get("counters_after") or {}).get("success")
            if (counters is None or counters["running"] or counters["waiting"]
                    or (baseline is not None and counters["success"] != baseline)):
                self.save(campaign_contaminated=True)
        from hearth.execution.coordination import GpuTenancyStore
        t = self.state.get("tenancy") or {}
        if not GpuTenancyStore(COORD_DB).release(resource=POOL, session_id=t["session_id"], epoch=int(t["epoch"]),
                                                 owner="experiment", restoration_verified=True,
                                                 reason=f"experiment {self.id} restored and verified"):
            raise RuntimeError(f"release matched no fence row (session {t['session_id']} epoch {t['epoch']}): fence lost or stolen")
        self.save("released")
        self.log("tenancy released")

    # --- the run -----------------------------------------------------------------------
    def run(self) -> int:
        if self.reused:
            print(f"[experiment {self.id}] refusing: {self.state_path} exists (phase {self.state.get('phase')}); use a new id",
                  flush=True)
            return 1

        def interrupted(signum: int, _frame: Any) -> None:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            raise KeyboardInterrupt(f"signal {signum}")
        signal.signal(signal.SIGTERM, interrupted)   # SIGTERM/^C before restore -> the restore path below
        outcome = "failed"
        try:
            self.preflight()
            self.acquire()
            self.preflight()   # again under the fence: a lease or request that slipped in before acquire
            self.take_snapshot()
            self.swap()
            self.campaign()
            outcome = "succeeded" if self.state.get("campaign_rc") == 0 else ("model_failed" if self.state.get("campaign_rc") == 4 else "failed")
            if (self.state.get("foreign_requests") is None or self.state.get("foreign_requests") or self.state.get("calls_reported") is None
                    or self.state.get("tenancy_renew_failures")):
                outcome = "void"   # the seat saw requests the campaign did not make (or cannot tell), or the fence lapsed
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 -- everything below still restores
            self.log("phase failed", error=f"{type(exc).__name__}: {exc}")
            outcome = "failed"
            if self.state.get("phase") in ("created", "acquired") or not self.state.get("resident"):
                # nothing was swapped; release the fence if we hold it and stop
                if self.state.get("tenancy"):
                    try:
                        self.save("restored"); self.release()
                    except Exception as rexc:  # noqa: BLE001
                        self.log("release failed", error=str(rexc))
                self.save("done", outcome=outcome)
                return 1
        # restore always runs once a swap was attempted; it is not interruptible (a half restore is worse)
        signal.signal(signal.SIGTERM, signal.SIG_IGN); signal.signal(signal.SIGINT, signal.SIG_IGN)
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
        if self.state.get("seat_restarted_externally") or self.state.get("campaign_contaminated"):
            outcome = "void"
        self.save("done", outcome=outcome, finished=utc())
        self.log("done", outcome=outcome)
        return 0 if outcome == "succeeded" else 1


class PairedExperiment(Experiment):
    """One fence and campaign, independent persisted seat lifecycles.

    Spec: seats=[0,1], dropin={"0": null, "1": "zz-arm.conf"},
    backend={"0": "omen-dense-27b", "1": "omen-dense-27b-b"}.
    expect_argv and expect_model are optional maps keyed by seat as well.
    The campaign's last JSON report must carry calls={"0": N, "1": N}.
    """
    def __init__(self, spec: dict[str, Any]) -> None:
        if spec.get("seats") != [0, 1] or "seat" in spec:
            raise ValueError("paired specs require seats=[0,1] and no seat")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", str(spec["id"])):
            raise ValueError("paired experiment id must be a simple filename")
        for key in ("dropin", "backend"):
            if not isinstance(spec.get(key), dict) or set(spec[key]) != {"0", "1"}:
                raise ValueError(f"paired {key} requires exactly keys 0 and 1")
        if any(not isinstance(b, str) or not b for b in spec["backend"].values()):
            raise ValueError("paired backends must be explicit nonempty names")
        if len(set(spec["backend"].values())) != 2:
            raise ValueError("paired backends must be distinct")
        for key in ("expect_argv", "expect_model"):
            if key in spec and (not isinstance(spec[key], dict) or set(spec[key]) != {"0", "1"}):
                raise ValueError(f"paired {key} requires exactly keys 0 and 1")
        for dropin in spec["dropin"].values():
            if dropin is not None and (not isinstance(dropin, str) or not dropin or Path(dropin).name != dropin):
                raise ValueError("dropin must be a filename or null")
        super().__init__({"id": spec["id"], "seat": 0, "dropin": None})
        self.spec = spec
        self.state["spec"] = spec
        self.state["schema"] = "experiment-linux.paired.v1"
        self.members = {}
        for seat in spec["seats"]:
            seat_spec = {"id": f"{self.id}/seat-{seat}", "seat": seat}
            for key in ("dropin", "backend", "expect_argv", "expect_model"):
                if key in spec:
                    seat_spec[key] = spec[key][str(seat)]
            self.members[str(seat)] = Experiment(seat_spec)
        self.reused = self.reused or any(m.reused for m in self.members.values())

    def preflight(self) -> None:
        path = Path(os.environ.get("HEARTH_BACKENDS", str(HEARTH_ROOT / "backends-linux.toml")))
        entries = tomllib.loads(path.read_text())["backend"]
        for seat, member in self.members.items():
            matches = [b for b in entries if b["name"] == member.spec["backend"]]
            expected = f"http://127.0.0.1:{18095 + int(seat)}"
            if len(matches) != 1 or matches[0]["endpoint"].rstrip("/") != expected:
                raise RuntimeError(f"backend {member.spec['backend']} does not bind seat {seat} at {expected}")
            member.preflight()

    def take_snapshot(self) -> None:
        # All snapshots precede either swap. Partial snapshots never imply a swap.
        for member in self.members.values():
            member.take_snapshot()
        self.save("snapshot", resident={k: m.state["resident"] for k, m in self.members.items()})

    def swap(self) -> None:
        for member in self.members.values():
            if member.dropin is None:
                argv = member.record_argv(running_must_match=True)
                member.refuse_busy()
                member.save("restarting", seat_restarted=True, cold_restart_pending=True)
                member.restart_and_wait()
                member.check_running_args(argv)
                member.save(cold_restart_pending=False)
            member.swap()
        self.save("swapped-verified", effective_argv={k: m.state["effective_argv"] for k, m in self.members.items()},
                  served_after_swap={k: m.state["served_after_swap"] for k, m in self.members.items()})

    def seat_counters(self) -> dict:
        return {k: m.seat_counters() for k, m in self.members.items()}

    def _calls_reported(self, offset: int):
        lines = (self.dir / "campaign.out").read_bytes()[offset:].decode("utf-8", "replace").splitlines()
        for line in reversed(lines):
            try:
                calls = json.loads(line)["calls"]
            except (ValueError, KeyError, TypeError):
                continue
            if (isinstance(calls, dict) and set(calls) == set(self.members)
                    and all(type(n) is int and n >= 0 for n in calls.values())):
                return calls
            return None
        return None

    def foreign_requests(self, before, after, calls):
        per_seat = {k: None if calls is None or after is None or after.get(k) is None else int(after[k]["success"] - before[k]["success"]) - calls[k]
                    for k in self.members}
        self.save(foreign_requests_by_seat=per_seat)
        # Absolute values prevent opposite differences on the cards cancelling.
        return None if any(v is None for v in per_seat.values()) else sum(abs(v) for v in per_seat.values())

    def wait_drained(self, timeout_s: float = 3600) -> dict:
        return {k: m.wait_drained(timeout_s) for k, m in self.members.items()}

    def restore(self) -> None:
        errors = {}
        for key, member in self.members.items():
            if member.state.get("phase") == "restored":
                continue
            try:
                counters = member.sample_counters()
                member.save(counters_restore_entry=counters)
                member.restore()
                member.save(counters_restored=member.sample_counters())
            except BaseException as exc:
                errors[key] = f"{type(exc).__name__}: {exc}"
        self.save(restoration_by_seat={k: m.state for k, m in self.members.items()})
        if errors:
            raise RuntimeError(f"paired restoration incomplete: {errors}")
        if any(m.state.get("seat_restarted_externally") for m in self.members.values()):
            self.save(seat_restarted_externally=True)
        if self.state.get("counters_before"):
            settled = {k: m.state["counters_restore_entry"] for k, m in self.members.items()}
            foreign = self.foreign_requests(self.state["counters_before"], settled, self.state.get("calls_reported"))
            self.save(counters_settled=settled, foreign_requests=foreign)
            if foreign is None or foreign:
                self.save(campaign_contaminated=True)
        self.save("restored")

    def release(self) -> None:
        if not self.state.get("resident"):
            return super().release()
        settled = {k: m.sample_counters() for k, m in self.members.items()}
        extra = {}
        for key, member in self.members.items():
            entry = member.state.get("counters_restore_entry")
            if entry is None:
                self.save(campaign_contaminated=True)
                continue
            # A treatment is restarted during restore; all its new requests are foreign.
            baseline = 0 if member.state.get("restoration_restarted") else entry["success"]
            extra[key] = None if settled[key] is None else settled[key]["success"] - baseline
            if settled[key] is None or settled[key]["running"] or settled[key]["waiting"]:
                self.save(campaign_contaminated=True)
        if any(extra.values()):
            self.save(campaign_contaminated=True)
        self.save(requests_during_restore=extra, counters_release=settled)
        super().release()


def status(exp_id: str) -> dict[str, Any]:
    p = EXP_ROOT / exp_id / "state.json"
    if not p.exists():
        return {"id": exp_id, "phase": "missing"}
    d = json.loads(p.read_text())
    return {k: d.get(k) for k in ("id", "phase", "outcome", "started", "updated", "campaign_rc", "resident", "served_after_swap",
                                      "effective_argv", "counters_before", "counters_after", "calls_reported",
                                      "foreign_requests", "foreign_requests_by_seat", "restoration_by_seat")}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--spec", required=True)
    s = sub.add_parser("status"); s.add_argument("--id", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "status":
        print(json.dumps(status(args.id), indent=2, default=str)); return 0
    spec = json.loads(Path(args.spec).read_text())
    return (PairedExperiment(spec) if "seats" in spec else Experiment(spec)).run()


if __name__ == "__main__":
    raise SystemExit(main())

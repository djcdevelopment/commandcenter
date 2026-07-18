"""HEARTH tool provider: fleet build harvest (un-strand fleet results to GitHub).

The problem this closes (the capture hole, 2026-07-17): the mechnet fleet builds
each candidate on a worker node and the winner/lap branches
(``ccfarm/<plan_id>/<worker>/lap1``) land only in the conductor's *farmer-repo*
— a working repo on cc-conductor whose only git remote is a dead-end local bare.
The conductor has no path to GitHub (no HTTPS creds, SSH host-key unverified), so
fleet CODE never reaches the central remote. To see what a builder actually did
you had to SSH-hop to the conductor and read refs out of the farmer-repo — work
that never crosses the manifested HEARTH door and so is never captured.
(``task_status`` returns only ``result.json``, not the branch.)

The fix is OMEN as the bridge, NOT the conductor. OMEN already has (a) working
GitHub push credentials (every ``git_commit_push`` rides them) and (b) SSH to the
conductor (every ``submit_task``/``task_status`` rides it). So this provider,
running in the gateway on OMEN, fetches a run's ``ccfarm`` branches from the
conductor's farmer-repo and pushes them to the (private) GitHub ``origin`` under a
``fleet/<plan_id>/<worker>/lap1`` namespace. After that, ``git fetch origin`` from
any machine retrieves the build — no SSH-hop, and the harvest itself is a
manifested door call, so it lands on the ledger like everything else (the whole
point: keep work ON the front door — see project-centralized-capture-principle).

Design guardrails:
  - Read-only on the conductor: we only ``fetch`` from the farmer-repo; we never
    git-operate on that concurrently-owned box (mirrors the task_lane contract).
  - Non-force push under a dedicated ``fleet/`` namespace; ``master``/``main`` are
    never touched. Build branches are immutable, so a re-harvest is a no-op.
  - GitHub creds and the push both stay on OMEN, where they already work.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from typing import Callable, Optional

from hearth.toolsurface._scope import scope_root

# The RUNNING conductor's farmer-repo (verified from /proc/<pid>/environ,
# FARMER_REPO_PATH, 2026-07-17 — NOT the default BASE/farmer-repo, which the
# 07-09 mshome migration left stale). ssh:// URL so plain `git fetch` reaches it.
CONDUCTOR_SSH = "claude@cc-conductor.mshome.net"  # mshome LAN, off the tailnet (ADR-0014)
FARMER_REPO_PATH = "/home/claude/work/commandcenter-ontology/farmer-repo"
FARMER_URL = f"ssh://{CONDUCTOR_SSH}{FARMER_REPO_PATH}"

SOURCE_PREFIX = "ccfarm"          # branch namespace the fleet writes
DEST_PREFIX = "fleet"             # branch namespace we mirror into on origin
STAGE_PREFIX = "refs/fleet-harvest"  # local staging refs (cleaned up every run)
ORIGIN = "origin"

# plan_id flows into a git refspec — keep it to a strict, glob-free charset so a
# crafted id can neither inject a refspec nor over-match with '*'.
_PLAN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _git(args: list[str], timeout_s: float = 60,
         runner: Optional[Callable[..., subprocess.CompletedProcess]] = None
         ) -> tuple[Optional[str], Optional[str]]:
    """Run one git command in the OMEN commandcenter repo. Returns (stdout, error).

    `runner` defaults to `subprocess.run` looked up at call time (not bound as a
    default arg) so tests can `patch("subprocess.run", ...)`, matching the
    task_lane/occupancy pattern.
    """
    active = runner or subprocess.run
    try:
        cp = active(
            ["git", "-C", str(scope_root()), *args],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if cp.returncode != 0:
        sub = args[0] if args else "?"
        return None, f"git {sub} exit {cp.returncode}: {(cp.stderr or '').strip()[:300]}"
    return cp.stdout, None


def harvest_fleet_run(plan_id: str) -> dict:
    """Un-strand one fleet run: mirror its ``ccfarm/<plan_id>/*`` branches from the
    conductor farmer-repo to GitHub ``origin`` under ``fleet/<plan_id>/<worker>/lap1``.

    Fetch (read-only on the conductor) → push (non-force, ``fleet/`` namespace) →
    clean up local staging refs. Idempotent: build branches are immutable, so a
    second harvest is a no-op push. Returns
    ``{ok, plan_id, github_prefix, count, workers:[{worker_ref, sha, github_branch}]}``;
    a run with no branches on the farmer-repo is ``ok:true`` with ``workers:[]`` and
    a note (not an error). After this, ``git fetch origin`` retrieves the code
    anywhere — no SSH-hop.
    """
    if not isinstance(plan_id, str) or not _PLAN_ID_RE.match(plan_id or ""):
        raise ValueError("plan_id must match [A-Za-z0-9._-]+ (no slashes or glob chars)")

    src = f"refs/heads/{SOURCE_PREFIX}/{plan_id}/*"
    stage = f"{STAGE_PREFIX}/{plan_id}/*"
    stage_root = f"{STAGE_PREFIX}/{plan_id}"

    # 1. Fetch the run's branches from the conductor farmer-repo into staging refs.
    _, err = _git(["fetch", "--no-tags", FARMER_URL, f"{src}:{stage}"], timeout_s=120)
    if err is not None:
        return {"ok": False, "plan_id": plan_id, "stage": "fetch", "error": err}

    # 2. Enumerate what actually arrived.
    out, err = _git(["for-each-ref", "--format=%(objectname) %(refname)", stage_root])
    if err is not None:
        return {"ok": False, "plan_id": plan_id, "stage": "enumerate", "error": err}
    staged: list[tuple[str, str]] = []
    for line in (out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        sha, _, ref = line.partition(" ")
        if sha and ref:
            staged.append((sha, ref))

    if not staged:
        return {"ok": True, "plan_id": plan_id, "workers": [], "count": 0, "pushed": False,
                "note": f"no {SOURCE_PREFIX}/{plan_id} branches on the conductor farmer-repo"}

    # 3. Push to origin under the fleet/ namespace (non-force — build branches are immutable).
    _, perr = _git(["push", ORIGIN, f"{stage}:refs/heads/{DEST_PREFIX}/{plan_id}/*"], timeout_s=180)

    # 4. Always clean up local staging refs, whether the push succeeded or not.
    for _sha, ref in staged:
        _git(["update-ref", "-d", ref])

    if perr is not None:
        return {"ok": False, "plan_id": plan_id, "stage": "push", "error": perr,
                "found": len(staged)}

    workers = []
    for sha, ref in staged:
        tail = ref.split(f"{stage_root}/", 1)[-1]  # <worker>/lap1
        workers.append({
            "worker_ref": tail, "sha": sha,
            "github_branch": f"{DEST_PREFIX}/{plan_id}/{tail}",
        })
    return {
        "ok": True, "plan_id": plan_id, "source": FARMER_URL,
        "github_prefix": f"{DEST_PREFIX}/{plan_id}", "pushed": True,
        "count": len(workers), "workers": workers,
    }


def list_fleet_runs(limit: int = 20) -> dict:
    """Discover harvestable fleet runs: distinct ``plan_id``s that have
    ``ccfarm/*`` branches on the conductor farmer-repo, with a per-run branch
    count. Ordering is lexical by plan_id (ls-remote carries no dates), so use it
    to find a run whose id you don't have — not as a recency feed. Returns
    ``{ok, count, total_runs_on_farmer, runs:[{plan_id, branch_count}]}``.
    """
    if not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    out, err = _git(["ls-remote", FARMER_URL, f"refs/heads/{SOURCE_PREFIX}/*"], timeout_s=60)
    if err is not None:
        return {"ok": False, "error": err}
    counts: dict[str, int] = {}
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        rest = parts[1].split(f"refs/heads/{SOURCE_PREFIX}/", 1)[-1]  # <pid>/<worker>/lap1
        pid = rest.split("/", 1)[0]
        if pid:
            counts[pid] = counts.get(pid, 0) + 1
    runs = sorted(({"plan_id": p, "branch_count": n} for p, n in counts.items()),
                  key=lambda r: r["plan_id"], reverse=True)
    return {"ok": True, "count": min(len(runs), limit),
            "total_runs_on_farmer": len(runs), "runs": runs[:limit]}


def get_tools() -> list[Callable]:
    return [harvest_fleet_run, list_fleet_runs]


# --- Sweep entrypoint (the fleet_harvest gateway timer fires this) ----------
#
# ``python -m hearth.toolsurface.fleet_harvest --sweep --json`` harvests EVERY
# discoverable run in one pass so no build ever strands: the timer's first tick
# drains whatever backlog has accumulated, and every tick after is a cheap
# no-op re-mirror (branches are immutable, the push is non-force). Per-run
# failure is recorded and skipped, never aborting the sweep — resilience, per
# the kernel-timer contract (hearth/kernel/timers.py).

def _sweep(limit: int = 1000) -> dict:
    """Harvest every run list_fleet_runs can see. Never raises out of the
    per-run loop. Reports honest aggregates: ``branches_mirrored`` counts
    branches ensured-present on origin (NOT newly-pushed — the harvest push is
    a non-force no-op when the branch already exists, and we don't parse git's
    per-ref output; a true new-vs-noop delta would need ``git push --porcelain``
    surfaced from harvest_fleet_run, a clean follow-up)."""
    report: dict = {
        "ok": True, "runs_seen": 0, "runs_mirrored": 0, "runs_empty": 0,
        "branches_mirrored": 0, "errors": [],
    }
    listed = list_fleet_runs(limit=limit)
    if not listed.get("ok"):
        report["ok"] = False
        report["errors"].append(
            {"plan_id": "*", "error": listed.get("error", "list_fleet_runs failed")})
        return report
    runs = listed.get("runs", [])
    report["runs_seen"] = len(runs)
    for run in runs:
        plan_id = run.get("plan_id")
        if not plan_id:
            continue
        try:
            res = harvest_fleet_run(plan_id)
        except Exception as exc:  # one bad run must never abort the sweep
            report["errors"].append(
                {"plan_id": plan_id, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if not res.get("ok"):
            report["errors"].append(
                {"plan_id": plan_id, "error": res.get("error", "harvest failed")})
            continue
        count = res.get("count", 0)
        if count > 0:
            report["runs_mirrored"] += 1
            report["branches_mirrored"] += count
        else:
            report["runs_empty"] += 1
    if report["errors"]:
        report["ok"] = False
    return report


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m hearth.toolsurface.fleet_harvest",
        description="Sweep the conductor farmer-repo and mirror every fleet "
                    "run's ccfarm/* branches to the private GitHub origin "
                    "(idempotent).",
    )
    ap.add_argument("--sweep", action="store_true",
                    help="harvest every discoverable run (the timer entrypoint)")
    ap.add_argument("--json", action="store_true",
                    help="emit the sweep report as one JSON object")
    ap.add_argument("--limit", type=int, default=1000,
                    help="max runs to enumerate (default 1000 — covers all)")
    args = ap.parse_args(argv)
    if not args.sweep:
        ap.error("nothing to do: pass --sweep")

    report = _sweep(limit=args.limit)
    if args.json:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print(f"fleet_harvest sweep: seen={report['runs_seen']} "
              f"mirrored={report['runs_mirrored']} empty={report['runs_empty']} "
              f"branches={report['branches_mirrored']} errors={len(report['errors'])}")
    # Exit 0 when the sweep ran, even with per-run errors (recorded, not fatal —
    # resilience); exit 1 only when enumeration itself failed.
    enumerate_failed = any(e.get("plan_id") == "*" for e in report["errors"])
    return 1 if enumerate_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

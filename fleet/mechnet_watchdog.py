#!/usr/bin/env python3
"""mechnet-watchdog — self-healing sweep over the fleet inventory (Banked Fire P4).

Generalizes what doorcheck proved (kill/revive round-trip, 322ab7d): read
fleet/inventory.toml, probe every declared service, and for each service that is
DOWN on an expect="up" node AND carries a ``revive`` command, run that command
once, re-probe, and record the outcome on the HEARTH kernel ledger. Un-revivable
failures are recorded too, so a dead service surfaces on the ledger instead of
being discovered mid-dispatch.

    python -m fleet.mechnet_watchdog                 # one healing pass
    python -m fleet.mechnet_watchdog --dry-run       # plan only, run nothing
    python -m fleet.mechnet_watchdog --json          # machine-readable
    python -m fleet.mechnet_watchdog --no-ledger     # skip the ledger write

Design (Banked Fire):
- Per-CHECK revive, not per-node: a node can be up while one of its services is
  down (OMEN's Ollama up, gateway down), so revive attaches to the check.
- The kernel ledger (hearth/var/ledger) is the audit log, SEPARATE from the
  knowledge projection sources (runs/), so watchdog events never pollute beliefs.
- Runs on OMEN: always-on, sees the tailnet and the Hyper-V VM siblings at once.

Exit code: 0 if, after the pass, no expect="up" service is still down; else 1.
Stdlib only, plus hearth.kernel.ledger for the audit record (mcp-free).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

# Repo root so `fleet.*` and `hearth.*` import regardless of cwd.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fleet.fleet_ping import DEFAULT_INVENTORY, DEFAULT_TIMEOUT, load_inventory, probe

WATCHDOG_CALLER = {"id": "mechnet-watchdog", "runner_class": "human", "node": "omen"}
REVIVE_TIMEOUT_S = 60


def collect_checks(nodes: list[dict], timeout: float, prober=probe) -> list[dict]:
    """Probe every declared check on every node. One row per check.

    Each row: {node, service, host, port, expect, reachable, revive}. `prober`
    is injectable so tests run without a network (mirrors fleet_ping.sweep).
    """
    rows: list[dict] = []
    for node in nodes:
        expect = node.get("expect", "up")
        addr = node.get("address")
        for check in node.get("checks") or []:
            host = check.get("host") or addr
            port = int(check["port"])
            reachable, _latency, _err = prober(host, port, timeout)
            rows.append({
                "node": node.get("name", "?"),
                "service": check.get("service", "tcp"),
                "host": host,
                "port": port,
                "expect": expect,
                "reachable": reachable,
                "revive": check.get("revive"),
            })
    return rows


def plan_revivals(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split down expect="up" services into (revivable, alert_only).

    A service is a failure if its node expects up and it is unreachable. It is
    revivable when it also declares a `revive` command; otherwise alert-only.
    """
    downs = [r for r in rows if r["expect"] == "up" and not r["reachable"]]
    revivable = [r for r in downs if r.get("revive")]
    alert_only = [r for r in downs if not r.get("revive")]
    return revivable, alert_only


def _run_command(command: str) -> dict:
    """Run a revive command; return {exit_code, timed_out}. Never raises."""
    try:
        completed = subprocess.run(
            command, shell=True, cwd=str(_REPO_ROOT),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=REVIVE_TIMEOUT_S,
        )
        return {"exit_code": completed.returncode, "timed_out": False}
    except subprocess.TimeoutExpired:
        return {"exit_code": None, "timed_out": True}
    except OSError as exc:
        return {"exit_code": None, "timed_out": False, "error": str(exc)}


def revive_one(row: dict, timeout: float,
               runner: Callable[[str], dict] = _run_command, prober=probe) -> dict:
    """Run one revive command and re-probe the service. Returns an outcome row."""
    run_result = runner(row["revive"])
    reachable, _latency, _err = prober(row["host"], row["port"], timeout)
    return {
        "node": row["node"],
        "service": row["service"],
        "host": row["host"],
        "port": row["port"],
        "revive": row["revive"],
        "revive_result": run_result,
        "recovered": reachable,
    }


def _record(outcome: dict, ledger=None) -> Optional[str]:
    """Append one hearth-event.v1 for a revive attempt. Best-effort; a ledger
    failure (e.g. gateway holding the file) must not crash the watchdog."""
    try:
        from hearth.kernel.ledger import Ledger, new_event
        led = ledger or Ledger()
        return led.append(new_event(
            WATCHDOG_CALLER, "mechnet_watchdog.revive",
            args={"node": outcome["node"], "service": outcome["service"],
                  "revive": outcome["revive"]},
            result={"recovered": outcome["recovered"],
                    "revive_result": outcome["revive_result"]},
            ok=bool(outcome["recovered"]),
            error=None if outcome["recovered"] else "service still down after revive",
        ))
    except Exception as exc:  # audit is best-effort; never let it break healing
        print(f"[watchdog] ledger append failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return None


def run_pass(inventory_path: Path, timeout: float, dry_run: bool,
             write_ledger: bool, prober=probe,
             runner: Callable[[str], dict] = _run_command) -> dict:
    """One healing pass. Returns a JSON-serializable report."""
    inv = load_inventory(inventory_path)
    rows = collect_checks(inv["nodes"], timeout, prober=prober)
    revivable, alert_only = plan_revivals(rows)

    revivals: list[dict] = []
    for row in revivable:
        if dry_run:
            revivals.append({**{k: row[k] for k in ("node", "service", "host", "port", "revive")},
                             "dry_run": True})
            continue
        outcome = revive_one(row, timeout, runner=runner, prober=prober)
        if write_ledger:
            outcome["ledger_event_id"] = _record(outcome)
        revivals.append(outcome)

    still_down = [r for r in alert_only] + [
        o for o in revivals if not o.get("dry_run") and not o.get("recovered")]
    return {
        "checked": len(rows),
        "down": len(revivable) + len(alert_only),
        "revivable": len(revivable),
        "alert_only": alert_only,
        "revivals": revivals,
        "healthy": not still_down,
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--dry-run", action="store_true", help="plan revivals, run nothing")
    ap.add_argument("--no-ledger", action="store_true", help="skip the ledger audit write")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    report = run_pass(args.inventory, args.timeout, dry_run=args.dry_run,
                      write_ledger=not args.no_ledger)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"mechnet-watchdog: {report['checked']} services checked, "
              f"{report['down']} down ({report['revivable']} revivable)")
        for o in report["revivals"]:
            if o.get("dry_run"):
                print(f"  WOULD REVIVE {o['node']}/{o['service']}  ->  {o['revive']}")
            else:
                mark = "RECOVERED" if o.get("recovered") else "STILL DOWN"
                print(f"  {mark}  {o['node']}/{o['service']}")
        for a in report["alert_only"]:
            print(f"  ALERT (no revive declared)  {a['node']}/{a['service']} "
                  f"@ {a['host']}:{a['port']}")
        print("verdict: " + ("HEALTHY" if report["healthy"] else "DEGRADED"))

    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

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
    python -m fleet.mechnet_watchdog --no-watchfire  # liveness only, skip coherence sweep
    python -m fleet.mechnet_watchdog --patrol-only   # 5-min cadence: cheap gap-scan snapshot only

Each 15-min pass does three things: a LIVENESS heal (probe services, revive the
down ones), a COHERENCE sweep (Watchfire — hearth.toolsurface.masters_pet
auto-heals the obvious+reversible run-coherence gaps, e.g. phantom_in_flight,
and flags the ambiguous ones), and a PATROL-TREND lookback (reads the last 3 of
the separate 5-min --patrol-only snapshots and tags gaps persistent/new/
resolved by plain set comparison on (kind, plan_id) — no scoring, just "has
this shown up before"). Liveness stays the health gate (the exit code); the
coherence sweep and the trend lookback are both additive and best-effort —
their failure never fails the patrol.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# Repo root so `fleet.*` and `hearth.*` import regardless of cwd.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fleet.fleet_ping import DEFAULT_INVENTORY, DEFAULT_TIMEOUT, load_inventory, probe

WATCHDOG_CALLER = {"id": "mechnet-watchdog", "runner_class": "human", "node": "omen"}
REVIVE_TIMEOUT_S = 60

DEFAULT_SNAPSHOT_PATH = _REPO_ROOT / "hearth" / "var" / "mechnet_watchdog_patrol_snapshots.json"
SNAPSHOT_CONTRACT_VERSION = "mechnet-watchdog-patrol-snapshot.v1"
SNAPSHOT_CAP = 12   # ~1h buffer at a 5-min cadence
TREND_WINDOW = 3    # "the 3 stamps before it" -- one 15-min cycle's worth of 5-min ticks


def _default_snapshot_doc() -> dict:
    return {"contract_version": SNAPSHOT_CONTRACT_VERSION, "entries": []}


def load_snapshots(path: Path = DEFAULT_SNAPSHOT_PATH) -> dict:
    """Read the rolling patrol-snapshot file, defaulting to an empty doc if
    absent or corrupt (mirrors bankedfire_drain.load_arm_state: a bad file
    means 'no history yet', never a crash and never fabricated data)."""
    if not path.is_file():
        return _default_snapshot_doc()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_snapshot_doc()
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return _default_snapshot_doc()
    doc = _default_snapshot_doc()
    doc.update(data)
    return doc


def save_snapshots(doc: dict, path: Path = DEFAULT_SNAPSHOT_PATH) -> None:
    """Write the snapshot doc (mkdir parents; same indent+newline shape as
    bankedfire_drain.save_arm_state)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def _gap_key(gap: dict) -> tuple[str, str]:
    """Identity of a gap across snapshots: (kind, plan_id). Gap dicts carry no
    hash of their own (hearth.health.gaps.Gap is kind/severity/plan_id/detail),
    so this tuple IS the identity."""
    return (gap.get("kind", ""), gap.get("plan_id", ""))


def take_snapshot(patrol_fn: Optional[Callable] = None) -> dict:
    """One cheap, observer-only patrol(refresh=False) tick, shaped into a
    compact snapshot entry. `patrol_fn` is injectable so tests never hit real
    SSH (mirrors run_watchfire's `masters_pet_fn` injection point). Never raises:
    a patrol() failure becomes {ok: False, error: ...} in the entry, same
    discipline as run_watchfire wrapping masters_pet()."""
    try:
        if patrol_fn is None:
            from hearth.toolsurface.patrol import patrol as patrol_fn
        result = patrol_fn(refresh=False)
    except Exception as exc:  # snapshot-taking must never crash the 5-min tick
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    gaps = result.get("gaps") or []
    return {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "ok": bool(result.get("ok")),
        "scanned": result.get("scanned"),
        "considered": result.get("considered"),
        "gaps": gaps,
        "gap_keys": [list(_gap_key(g)) for g in gaps],
        "error": result.get("error"),
    }


def append_snapshot(entry: dict, path: Path = DEFAULT_SNAPSHOT_PATH,
                    cap: int = SNAPSHOT_CAP) -> dict:
    """Append one snapshot entry, trim to the last `cap` entries, persist,
    return the updated doc."""
    doc = load_snapshots(path)
    doc["entries"].append(entry)
    doc["entries"] = doc["entries"][-cap:]
    save_snapshots(doc, path)
    return doc


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


def _record_watchfire(result: dict, ledger=None) -> Optional[str]:
    """Append one hearth-event summarizing a Watchfire coherence sweep.

    Best-effort, like _record: a ledger failure must not crash the patrol. The
    per-heal detail lives in each healed run's self-documenting stub on the
    conductor; this is the patrol-level audit line."""
    try:
        from hearth.kernel.ledger import Ledger, new_event
        led = ledger or Ledger()
        summary = {
            "healed": len(result.get("healed") or []),
            "healable": len(result.get("healable") or []),
            "flagged": len(result.get("flagged") or []),
            "dry_run": result.get("dry_run"),
        }
        return led.append(new_event(
            WATCHDOG_CALLER, "mechnet_watchdog.watchfire",
            args={"dry_run": summary["dry_run"]},
            result=summary,
            ok=bool(result.get("ok")),
            error=result.get("error"),
        ))
    except Exception as exc:  # audit is best-effort; never break the patrol
        print(f"[watchdog] watchfire ledger append failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return None


def _record_patrol_snapshot(entry: dict, ledger=None) -> Optional[str]:
    """Append one hearth-event summarizing a 5-min patrol snapshot tick.
    Best-effort, same shape as _record_watchfire: counts only, never lets a
    ledger hiccup break the tick."""
    try:
        from hearth.kernel.ledger import Ledger, new_event
        led = ledger or Ledger()
        summary = {"gap_count": len(entry.get("gaps") or []),
                   "scanned": entry.get("scanned"),
                   "considered": entry.get("considered")}
        return led.append(new_event(
            WATCHDOG_CALLER, "mechnet_watchdog.patrol_snapshot",
            args={}, result=summary,
            ok=bool(entry.get("ok")), error=entry.get("error"),
        ))
    except Exception as exc:  # audit is best-effort; never break the snapshot tick
        print(f"[watchdog] patrol_snapshot ledger append failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return None


def _record_patrol_trend(trend: dict, ledger=None) -> Optional[str]:
    """Append one hearth-event summarizing the 15-min trend check. Best-effort,
    same shape as every other _record*: counts only, never breaks the pass."""
    try:
        from hearth.kernel.ledger import Ledger, new_event
        led = ledger or Ledger()
        summary = {
            "insufficient_history": trend.get("insufficient_history"),
            "sample_count": trend.get("sample_count"),
            "persistent_count": len(trend.get("persistent") or []),
            "new_count": len(trend.get("new") or []),
            "resolved_count": len(trend.get("resolved") or []),
        }
        return led.append(new_event(
            WATCHDOG_CALLER, "mechnet_watchdog.patrol_trend",
            args={}, result=summary,
            ok=bool(trend.get("ok")), error=trend.get("error"),
        ))
    except Exception as exc:  # audit is best-effort; never break the 15-min pass
        print(f"[watchdog] patrol_trend ledger append failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return None


def run_watchfire(dry_run: bool, write_ledger: bool, masters_pet_fn=None, ledger=None) -> dict:
    """Coherence sweep: auto-heal the obvious+reversible run-coherence gaps.

    Wraps hearth.toolsurface.masters_pet (phantom_in_flight -> stub, occupancy
    released, reversible + documented; ambiguous gaps stay flagged). Best-effort:
    a failure here (conductor unreachable, import error) is captured and MUST NOT
    crash the liveness patrol — coherence-healing is additive, liveness is the gate.
    `masters_pet_fn` is injectable so tests run without SSH.
    """
    try:
        if masters_pet_fn is None:
            from hearth.toolsurface.masters_pet import masters_pet as masters_pet_fn
        result = masters_pet_fn(apply=not dry_run)
    except Exception as exc:  # never let the coherence sweep break the patrol
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if write_ledger:
        result = dict(result)
        result["ledger_event_id"] = _record_watchfire(result, ledger=ledger)
    return result


def compute_trend(entries: list[dict], window: int = TREND_WINDOW) -> dict:
    """Plain set arithmetic over the last `window` snapshot entries, keyed on
    (kind, plan_id). No scoring, no thresholds beyond "have we seen `window`
    samples yet" -- deliberately not a bloodhound, just the obvious.

    Only computed once len(entries) >= window; otherwise insufficient_history
    is True and persistent/new/resolved are empty (never a false "persistent"
    off a single sample)."""
    recent = entries[-window:]
    if len(recent) < window:
        return {"ok": True, "insufficient_history": True, "sample_count": len(recent),
                "persistent": [], "new": [], "resolved": []}

    key_sets = [{tuple(k) for k in (e.get("gap_keys") or [])} for e in recent]
    latest_keys = key_sets[-1]
    earlier_union = set().union(*key_sets[:-1]) if len(key_sets) > 1 else set()
    persistent_keys = set.intersection(*key_sets)
    new_keys = latest_keys - earlier_union
    resolved_keys = earlier_union - latest_keys

    def _as_dicts(keys):
        return [{"kind": k, "plan_id": p} for k, p in sorted(keys)]

    return {"ok": True, "insufficient_history": False, "sample_count": len(recent),
            "persistent": _as_dicts(persistent_keys),
            "new": _as_dicts(new_keys),
            "resolved": _as_dicts(resolved_keys)}


def run_patrol_snapshot(write_ledger: bool, snapshot_path: Path = DEFAULT_SNAPSHOT_PATH,
                        patrol_fn: Optional[Callable] = None, ledger=None) -> dict:
    """One 5-min tick: take_snapshot -> append_snapshot -> best-effort ledger.
    This is the entire body of the `--patrol-only` CLI path."""
    entry = take_snapshot(patrol_fn=patrol_fn)
    doc = append_snapshot(entry, path=snapshot_path)
    ledger_event_id = _record_patrol_snapshot(entry, ledger=ledger) if write_ledger else None
    return {"entry": entry, "snapshot_count": len(doc["entries"]),
            "ledger_event_id": ledger_event_id}


def run_patrol_trend(write_ledger: bool, snapshot_path: Path = DEFAULT_SNAPSHOT_PATH,
                     ledger=None) -> dict:
    """Read the rolling snapshot file, compute the 3-sample trend, best-effort
    ledger it. Wrapped so a corrupt file or a compute_trend bug degrades to
    {ok: False, error} and NEVER fails the 15-min pass -- same discipline as
    run_watchfire wrapping masters_pet()."""
    try:
        doc = load_snapshots(snapshot_path)
        trend = compute_trend(doc["entries"])
    except Exception as exc:
        trend = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                 "insufficient_history": None, "sample_count": 0,
                 "persistent": [], "new": [], "resolved": []}
    if write_ledger:
        trend = dict(trend)
        trend["ledger_event_id"] = _record_patrol_trend(trend, ledger=ledger)
    return trend


def run_pass(inventory_path: Path, timeout: float, dry_run: bool,
             write_ledger: bool, prober=probe,
             runner: Callable[[str], dict] = _run_command,
             include_watchfire: bool = True, masters_pet_fn=None,
             include_patrol_trend: bool = True,
             snapshot_path: Path = DEFAULT_SNAPSHOT_PATH, ledger=None) -> dict:
    """One patrol pass: liveness heal + (optional) Watchfire coherence sweep +
    (optional) patrol-trend lookback.

    Returns a JSON-serializable report. `healthy` reflects LIVENESS only — the
    coherence sweep and the patrol-trend check are both additive and never
    flip the health verdict.
    """
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
    report = {
        "checked": len(rows),
        "down": len(revivable) + len(alert_only),
        "revivable": len(revivable),
        "alert_only": alert_only,
        "revivals": revivals,
        "healthy": not still_down,
    }
    if include_watchfire:
        report["watchfire"] = run_watchfire(dry_run, write_ledger, masters_pet_fn=masters_pet_fn)
    if include_patrol_trend:
        report["patrol_trend"] = run_patrol_trend(write_ledger, snapshot_path=snapshot_path,
                                                  ledger=ledger)
    return report


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--dry-run", action="store_true", help="plan revivals, run nothing")
    ap.add_argument("--no-ledger", action="store_true", help="skip the ledger audit write")
    ap.add_argument("--no-watchfire", action="store_true", help="liveness only, skip the coherence sweep")
    ap.add_argument("--no-patrol-trend", action="store_true", help="skip the 3-sample patrol trend check")
    ap.add_argument("--patrol-only", action="store_true",
                    help="5-min cadence: cheap patrol(refresh=False) snapshot only "
                         "-- no liveness probe, no masters_pet, no inventory load")
    ap.add_argument("--snapshot-path", type=Path, default=DEFAULT_SNAPSHOT_PATH)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    if args.patrol_only:
        result = run_patrol_snapshot(write_ledger=not args.no_ledger,
                                    snapshot_path=args.snapshot_path)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            entry = result["entry"]
            print(f"mechnet-watchdog patrol-only: ok={entry['ok']} "
                  f"gaps={len(entry['gaps'])} snapshot_count={result['snapshot_count']}")
        # A nonzero gap count is not a failure here -- this tick observes, it
        # doesn't gate (the 15-min task still owns the healthy/degraded verdict).
        # Exit 1 means the watch itself broke (patrol() raised or returned ok=False).
        return 0 if result["entry"]["ok"] else 1

    report = run_pass(args.inventory, args.timeout, dry_run=args.dry_run,
                      write_ledger=not args.no_ledger,
                      include_watchfire=not args.no_watchfire,
                      include_patrol_trend=not args.no_patrol_trend,
                      snapshot_path=args.snapshot_path)

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
        wf = report.get("watchfire")
        if wf is not None:
            if not wf.get("ok"):
                print(f"watchfire: sweep failed — {wf.get('error')}")
            elif wf.get("dry_run"):
                print(f"watchfire: WOULD heal {len(wf.get('healable') or [])} phantom(s), "
                      f"{len(wf.get('flagged') or [])} gap(s) flagged")
            else:
                print(f"watchfire: healed {len(wf.get('healed') or [])} phantom(s), "
                      f"{len(wf.get('flagged') or [])} gap(s) flagged")
        trend = report.get("patrol_trend")
        if trend is not None:
            if not trend.get("ok"):
                print(f"patrol-trend: check failed — {trend.get('error')}")
            elif trend.get("insufficient_history"):
                print(f"patrol-trend: insufficient history ({trend['sample_count']}/{TREND_WINDOW} snapshots)")
            else:
                print(f"patrol-trend: {len(trend['persistent'])} persistent, "
                      f"{len(trend['new'])} new, {len(trend['resolved'])} resolved")
        print("verdict: " + ("HEALTHY" if report["healthy"] else "DEGRADED"))

    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

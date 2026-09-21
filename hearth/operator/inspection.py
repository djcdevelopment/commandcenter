"""Capacity snapshots and the inspection bundle.

Named ``inspection.py`` rather than ``inspect.py`` so that nothing inside this
package shadows the standard library's ``inspect`` module.

What this module guarantees, because the whole gate rests on it:

  * **Immutable.** A snapshot file is written once, under its own identity, and
    never modified afterwards. A reader that wants current freshness computes it
    against its own clock and reports it under ``presentation`` — excluded from
    every identity — never by editing the file.
  * **Caller-neutral.** Nothing in a snapshot names a caller, a key, a profile or
    a prompt. Door results are read field by field; no raw tool result is ever
    copied in wholesale (``kernel_status`` returns the calling caller, which is
    exactly the kind of thing that must not leak into shared truth).
  * **Two horizons (D-106).** The planning window on ``CURRENT.json`` is 300 s;
    every dynamic field carries its own ``observed_at``, ``ttl_s`` and
    ``fresh_until``. There is no minimum-TTL ``expires_at``: a planning snapshot
    may carry a field that goes stale during deliberation, but it may never
    describe that field as fresh.
  * **Honest nulls.** Every probe is guarded on its own. One source timing out
    nulls its own fields with a reason and leaves every other field's freshness
    intact.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from hearth.operator import paths
from hearth.operator.canonical import (canonical_json, decimal_str, identity_of,
                                       parse_rfc3339, plus_seconds, rfc3339,
                                       sha256_hex, utc_now)
from hearth.operator.door import DoorCall, make_door, unavailable_door

CONTRACT_VERSION = "capacity-snapshot.v1"
BUNDLE_CONTRACT_VERSION = "inspection-bundle.v1"

# Which freshness class each fact belongs to (D-106). Declared beside the probe
# that produces it so a field can never quietly acquire a longer TTL than the
# thing it measures.
READINESS = "readiness"
OCCUPANCY = "occupancy"
REACHABILITY = "reachability"
TRIAL_RUNWAY = "trial_runway"


class InspectError(RuntimeError):
    """Raised when inspection cannot honestly answer: a missing, corrupt, or
    out-of-window CURRENT.json. Never resolved by silently capturing."""


# --------------------------------------------------------------------------- #
# field construction
# --------------------------------------------------------------------------- #

def make_field(value: Any, observed_at: datetime, ttl_s: int, source: str,
               reason: Optional[str] = None) -> dict:
    """One dynamic fact: ``{value, observed_at, ttl_s, fresh_until, source}``.

    ``fresh_until = observed_at + ttl_s``. A null value must carry a reason —
    "unknown" and "measured as nothing" are different answers.
    """
    if value is None and not reason:
        raise ValueError("a null field must carry the reason it is null")
    field = {
        "value": value,
        "observed_at": rfc3339(observed_at),
        "ttl_s": int(ttl_s),
        "fresh_until": rfc3339(plus_seconds(observed_at, int(ttl_s))),
        "source": source,
    }
    if reason:
        field["reason"] = reason
    return field


class FieldFactory:
    """Builds fields with the configured TTL for a freshness class."""

    def __init__(self, ttls: dict[str, int]) -> None:
        self.ttls = ttls

    def __call__(self, klass: str, value: Any, observed_at: datetime, source: str,
                 reason: Optional[str] = None) -> dict:
        if klass not in self.ttls:
            raise KeyError(f"no ttl configured for freshness class {klass!r}")
        return make_field(value, observed_at, self.ttls[klass], source, reason)


# --------------------------------------------------------------------------- #
# probes
# --------------------------------------------------------------------------- #

def default_cli_runner(argv: list[str], timeout_s: float = 30.0) -> dict:
    """Run a repository CLI and parse its JSON. Never raises."""
    try:
        completed = subprocess.run([sys.executable, *argv], cwd=str(paths.REPO_ROOT),
                                   capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"ok": False, "value": None,
                "error": f"timed out after {timeout_s:.0f}s: {' '.join(argv)}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "value": None, "error": f"{type(exc).__name__}: {exc}"}
    try:
        return {"ok": True, "value": json.loads(completed.stdout), "error": None}
    except ValueError:
        return {"ok": False, "value": None,
                "error": (f"{' '.join(argv)} exited {completed.returncode} without JSON: "
                          f"{(completed.stderr or completed.stdout or '').strip()[:200]}")}


def _door_reason(result: dict) -> str:
    return str(result.get("error") or "the door did not answer")


def _trial_runway_facts() -> dict:
    """Trial-credit runway from the declared budget and the recorded spend.

    Both are local files, so this answers even with the door down. It is the one
    capacity fact that is arithmetic rather than a probe.
    """
    budgets = paths.load_toml(paths.BACKENDS_PATH).get("trial", {}) or {}
    budget = int(budgets.get("budget_tokens") or 0)
    reserve = int(budgets.get("reserve_tokens") or 0)
    spent: Optional[int] = None
    reason: Optional[str] = None
    if paths.OFFLOAD_KNOWLEDGE_PATH.is_file():
        try:
            offload = json.loads(paths.OFFLOAD_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
            trial = (offload.get("per_class") or {}).get("trial") or {}
            spent = int(trial.get("tokens_in") or 0) + int(trial.get("tokens_out") or 0)
        except (ValueError, TypeError, OSError) as exc:
            reason = f"knowledge/offload.json unreadable ({type(exc).__name__})"
    else:
        reason = "knowledge/offload.json is absent, so recorded trial spend is unknown"
    suppressed = None if spent is None else bool(budget and spent >= budget - reserve)
    return {"budget_tokens": budget, "reserve_tokens": reserve,
            "tokens_spent": spent, "suppressed": suppressed, "reason": reason}


def _local_holds() -> dict:
    """OMEN hold files, probed read-only under --local.

    Read-only on purpose: the tenancy store is another writer's state, and an
    inspection that creates or migrates it would be writing to a surface it does
    not own.
    """
    sentinel = paths.hearth_root() / "var" / "arc-maintenance.stop"
    holds: list[dict] = []
    notes: list[str] = []
    if sentinel.is_file():
        holds.append({"id": "arc-maintenance.stop", "kind": "sentinel",
                      "detail": "ArcServe start/stop is held; the shared maintenance lock is set",
                      "path": str(sentinel)})
    coordination = paths.hearth_root() / "var" / "coordination.sqlite"
    if coordination.is_file():
        try:
            import sqlite3

            uri = f"file:{coordination.as_posix()}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=5) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT resource, owner, state, epoch FROM gpu_tenancy").fetchall()
            for row in rows:
                holds.append({"id": f"gpu-tenancy:{row['resource']}", "kind": "gpu_tenancy",
                              "owner": str(row["owner"]), "state": str(row["state"]),
                              "epoch": int(row["epoch"])})
        except Exception as exc:  # noqa: BLE001
            notes.append(f"gpu tenancy store unreadable ({type(exc).__name__})")
    else:
        notes.append("no coordination store beside the hearth root")
    return {"holds": holds, "notes": notes}


# --------------------------------------------------------------------------- #
# capture
# --------------------------------------------------------------------------- #

def capture(catalog: dict, *, door: Optional[DoorCall] = None,
            cli_runner: Optional[Callable[..., dict]] = None,
            local: bool = False, now: Optional[datetime] = None,
            ttls: Optional[dict[str, int]] = None,
            planning_validity_s: Optional[int] = None,
            key: Optional[str] = None) -> dict:
    """Observe capacity once and return an immutable ``capacity-snapshot.v1``."""
    observed = now or utc_now()
    ttls = ttls or paths.field_ttls()
    validity = int(planning_validity_s if planning_validity_s is not None
                   else paths.planning_validity_s())
    call = door if door is not None else make_door(key)
    run_cli = cli_runner if cli_runner is not None else default_cli_runner
    fld = FieldFactory(ttls)

    catalog_version = str(catalog["catalog_version"])
    rung_ids = [rung["id"] for rung in catalog.get("rungs", [])]
    retired = {rung["id"] for rung in catalog.get("rungs", []) if rung.get("retired")}

    # --- door liveness ------------------------------------------------------
    status = call("kernel_status")
    status_at = utc_now()
    reachable = bool(status.get("ok"))
    providers: Optional[list[str]] = None
    if reachable and isinstance(status.get("value"), dict):
        raw_providers = status["value"].get("providers")
        if isinstance(raw_providers, list):
            providers = sorted(str(name) for name in raw_providers)
    door_block = {
        "reachable": fld(READINESS, reachable, status_at, "door:kernel_status",
                         None if reachable else _door_reason(status)),
        # kernel_status carries no version field; the mounted provider list is the
        # door's identity, so that is what is recorded rather than an invented number.
        "kernel_version": fld(READINESS, None, status_at, "door:kernel_status",
                              "kernel_status reports no version field; providers_mounted "
                              "is the door's identity"),
        "providers_mounted": fld(READINESS, providers, status_at, "door:kernel_status",
                                 None if providers is not None
                                 else _door_reason(status) if not reachable
                                 else "kernel_status returned no provider list"),
    }

    # --- rung readiness and residency --------------------------------------
    capture_result = call("capture_resource_snapshot")
    capture_at = utc_now()
    serve_truth = capture_result.get("value") if capture_result.get("ok") else None
    if not isinstance(serve_truth, dict):
        serve_truth = None
        capture_reason = _door_reason(capture_result) if not capture_result.get("ok") \
            else "capture_resource_snapshot returned no rung map"
    else:
        capture_reason = None

    rung_state = call("query_rung_state", rung="omen-arc")
    rung_state_at = utc_now()
    verdict = None
    if rung_state.get("ok") and isinstance(rung_state.get("value"), dict):
        payload = rung_state["value"]
        verdict = {"verdict": str(payload.get("verdict")),
                   "summary": str(payload.get("summary") or "")}

    providers_live = call("list_execution_providers")
    providers_live_at = utc_now()
    declared: Optional[set[str]] = None
    if providers_live.get("ok") and isinstance(providers_live.get("value"), list):
        declared = {str(row.get("name")) for row in providers_live["value"]
                    if isinstance(row, dict)}

    rotation = call("rotation_status")
    rotation_at = utc_now()
    rotation_value = rotation.get("value") if rotation.get("ok") else None
    if not isinstance(rotation_value, dict):
        rotation_value = None

    rungs: dict[str, dict] = {}
    residency_by_model: dict[str, list[str]] = {}
    local_snap: Optional[dict] = None
    for rung_id in sorted(rung_ids):
        row = (serve_truth or {}).get(rung_id)
        # The door (the running gateway) may predate a rung's stanza; the
        # in-process probe covers it, and the source label says so out loud.
        row_source = "door:capture_resource_snapshot"
        if not isinstance(row, dict) and rung_id not in retired:
            if local_snap is None:
                try:
                    from hearth.toolsurface import scheduler
                    local_snap = scheduler.capture_resource_snapshot()
                except Exception:
                    local_snap = {}
            if isinstance(local_snap.get(rung_id), dict):
                row = local_snap[rung_id]
                row_source = "local:capture_resource_snapshot"
        if isinstance(row, dict):
            ready_field = fld(READINESS, bool(row.get("ready")), capture_at,
                              row_source,
                              str(row.get("reason")) if row.get("reason") else None)
            loaded = row.get("loaded_models")
            residency_field = fld(
                READINESS,
                sorted(str(model) for model in loaded) if isinstance(loaded, list) else None,
                capture_at, row_source,
                None if isinstance(loaded, list)
                else "this rung's serve-truth probe reports no loaded-model list")
            slots = row.get("parallel_slots")
            slots_field = fld(OCCUPANCY,
                              int(slots) if isinstance(slots, int) else None,
                              capture_at, row_source,
                              None if isinstance(slots, int)
                              else "the serve-truth probe reports no slot count for this rung")
            if isinstance(loaded, list):
                for model in loaded:
                    residency_by_model.setdefault(str(model), []).append(rung_id)
        else:
            absent = (capture_reason
                      or ("the rung is retired: a tombstone, not an offer"
                          if rung_id in retired
                          else "capture_resource_snapshot does not cover this rung"))
            ready_field = fld(READINESS, None, capture_at,
                              "door:capture_resource_snapshot", absent)
            residency_field = fld(READINESS, None, capture_at,
                                  "door:capture_resource_snapshot", absent)
            slots_field = fld(OCCUPANCY, None, capture_at,
                              "door:capture_resource_snapshot", absent)

        if rung_id == "omen-arc" and verdict is not None:
            state_field = fld(READINESS, verdict, rung_state_at, "door:query_rung_state")
        elif rung_id == "omen-arc":
            state_field = fld(READINESS, None, rung_state_at, "door:query_rung_state",
                              _door_reason(rung_state))
        else:
            state_field = fld(READINESS, None, rung_state_at, "door:query_rung_state",
                              "ADR-0044 rate baselines exist for the production rung only")

        rungs[rung_id] = {
            "ready": ready_field,
            "rung_state": state_field,
            "residency": residency_field,
            "occupancy": fld(
                OCCUPANCY, None, capture_at, "none",
                "no door tool reports occupancy; the in-process /slots probe needs the "
                "rung bearer token, which the operator package must not read"),
            "free_vram_gb": fld(
                OCCUPANCY, None, capture_at, "none",
                "no live free-VRAM probe is wired into the control plane in G1"),
            "active_slots": slots_field,
            "declared_live": fld(
                READINESS,
                (rung_id in declared) if declared is not None else None,
                providers_live_at, "door:list_execution_providers",
                None if declared is not None else _door_reason(providers_live)),
        }

    models = {
        model: {"resident_on": fld(READINESS, sorted(set(rung_list)), capture_at,
                                   "door:capture_resource_snapshot")}
        for model, rung_list in sorted(residency_by_model.items())
    }

    # --- gpus ---------------------------------------------------------------
    gpus: dict[str, dict] = {}
    for host in catalog.get("hosts", []):
        for gpu in host.get("gpus", []):
            gpu_id = f"{host['id']}:{gpu.get('bdf') or gpu.get('type')}"
            gpus[gpu_id] = {
                "free_vram_gb": fld(
                    OCCUPANCY, None, observed, "none",
                    "no live free-VRAM probe is wired into the control plane in G1; the "
                    "catalog carries the card's total VRAM, which is not free VRAM"),
            }

    # --- reachability -------------------------------------------------------
    sweep = run_cli(["-m", "fleet.fleet_ping", "--all-services", "--json"])
    sweep_at = utc_now()
    hosts: dict[str, dict] = {}
    sweep_value = sweep.get("value") if sweep.get("ok") else None
    sweep_reason = None if sweep_value else (sweep.get("error") or "the sweep returned nothing")
    nodes = (sweep_value or {}).get("nodes") if isinstance(sweep_value, dict) else None
    by_name = {str(node.get("name")): node for node in nodes or [] if isinstance(node, dict)}
    for host in catalog.get("hosts", []):
        node = by_name.get(host["id"])
        if node is None:
            reason = sweep_reason or "the reachability sweep did not report this host"
            hosts[host["id"]] = {
                "reachable": fld(REACHABILITY, None, sweep_at,
                                 "cli:fleet.fleet_ping --all-services --json", reason),
                "services": fld(REACHABILITY, None, sweep_at,
                                "cli:fleet.fleet_ping --all-services --json", reason),
            }
            continue
        probes = [probe for probe in node.get("probes", []) or [] if isinstance(probe, dict)]
        services = {str(probe.get("service")): bool(probe.get("reachable")) for probe in probes}
        primary = str(probes[0].get("service")) if probes else "none declared"
        hosts[host["id"]] = {
            # fleet_ping's `reachable` is its PRIMARY declared service probe, not
            # host liveness: OMEN reads "down" whenever :8082 is not serving, on
            # the very machine running this capture. Say which port answered.
            "reachable": fld(REACHABILITY, bool(node.get("reachable")), sweep_at,
                             "cli:fleet.fleet_ping --all-services --json",
                             f"fleet_ping status {node.get('status')}: the primary declared "
                             f"service probe ({primary}); a closed port is not a dark host"),
            "services": fld(REACHABILITY, dict(sorted(services.items())), sweep_at,
                            "cli:fleet.fleet_ping --all-services --json"),
        }
    summary = (sweep_value or {}).get("summary") if isinstance(sweep_value, dict) else None
    reachability = {
        "sweep": fld(REACHABILITY,
                     {key: int(value) for key, value in sorted(summary.items())}
                     if isinstance(summary, dict) else None,
                     sweep_at, "cli:fleet.fleet_ping --all-services --json",
                     None if isinstance(summary, dict) else sweep_reason
                     or "the sweep returned no summary"),
    }

    # --- leases and holds ---------------------------------------------------
    leases: Optional[list[dict]] = None
    lease_reason: Optional[str] = None
    holds: list[dict] = []
    hold_reasons: list[str] = []
    if rotation_value is not None:
        leases = [{"id": f"rotation-window:{name}", "kind": "rotation_window",
                   "detail": "an open rotation window holds the B70s for side models"}
                  for name in sorted(str(w) for w in rotation_value.get("open_windows") or [])]
        tenancy = rotation_value.get("tenancy") or {}
        if isinstance(tenancy, dict) and tenancy.get("image_session"):
            holds.append({"id": "imagegen-tenancy", "kind": "gpu_tenancy",
                          "detail": "image generation owns the two-B70 pool (ArcServe is fenced)"})
        elif isinstance(tenancy, dict) and tenancy.get("readable") is False:
            hold_reasons.append("the imagegen tenancy fence was unreadable")
    else:
        lease_reason = _door_reason(rotation)
        hold_reasons.append(lease_reason)

    lanes = call("list_image_lanes")
    lanes_at = utc_now()
    if lanes.get("ok") and isinstance(lanes.get("value"), dict):
        session = lanes["value"].get("session")
        state = None
        if isinstance(session, dict):
            state = session.get("state") or session.get("status")
        if state and str(state).lower() not in {"idle", "stopped", "absent", "none"}:
            holds.append({"id": "image-session", "kind": "image_session",
                          "detail": f"image session state {state}"})
    else:
        hold_reasons.append(_door_reason(lanes))

    if local:
        probed = _local_holds()
        holds.extend(probed["holds"])
        hold_reasons.extend(probed["notes"])

    holds.sort(key=lambda row: row["id"])
    holds_field = fld(OCCUPANCY, holds, lanes_at,
                      "door:rotation_status + door:list_image_lanes"
                      + (" + local hold files" if local else ""),
                      "; ".join(hold_reasons) or None)
    leases_field = fld(OCCUPANCY, leases, rotation_at, "door:rotation_status", lease_reason)

    # --- trial runway -------------------------------------------------------
    runway = _trial_runway_facts()
    runway_at = utc_now()
    runway_source = "hearth/etc/backends.toml [trial] + knowledge/offload.json"
    trial_runway = {
        "budget_tokens": fld(TRIAL_RUNWAY, runway["budget_tokens"], runway_at, runway_source),
        "reserve_tokens": fld(TRIAL_RUNWAY, runway["reserve_tokens"], runway_at, runway_source),
        "tokens_spent": fld(TRIAL_RUNWAY, runway["tokens_spent"], runway_at, runway_source,
                            runway["reason"]),
        "suppressed": fld(TRIAL_RUNWAY, runway["suppressed"], runway_at, runway_source,
                          runway["reason"]),
    }

    snapshot = {
        "contract_version": CONTRACT_VERSION,
        "kind": "planning",
        "catalog_version": catalog_version,
        "observed_at": rfc3339(observed),
        "planning_valid_until": rfc3339(plus_seconds(observed, validity)),
        "planning_validity_s": validity,
        "door": door_block,
        "hosts": hosts,
        "gpus": gpus,
        "rungs": rungs,
        "models": models,
        "leases": leases_field,
        "holds": holds_field,
        "reachability": reachability,
        "trial_runway": trial_runway,
    }
    snapshot["snapshot_id"] = identity_of(snapshot, "snapshot_id")
    return snapshot


# --------------------------------------------------------------------------- #
# material change, freshness, persistence
# --------------------------------------------------------------------------- #

MATERIAL_FIELDS = ("readiness", "residency", "holds", "leases", "reachability")


def material_projection(snapshot: dict) -> dict:
    """The fields whose change invalidates a snapshot: readiness, residency,
    holds, leases, reachability. Values only — a later observation of the same
    world is not a change."""
    return {
        "readiness": {name: rung["ready"]["value"]
                      for name, rung in sorted(snapshot.get("rungs", {}).items())},
        "residency": {name: rung["residency"]["value"]
                      for name, rung in sorted(snapshot.get("rungs", {}).items())},
        "holds": (snapshot.get("holds") or {}).get("value"),
        "leases": (snapshot.get("leases") or {}).get("value"),
        "reachability": {name: host["reachable"]["value"]
                         for name, host in sorted(snapshot.get("hosts", {}).items())},
        "door_reachable": ((snapshot.get("door") or {}).get("reachable") or {}).get("value"),
    }


def material_digest(snapshot: dict) -> str:
    return sha256_hex(canonical_json(material_projection(snapshot)))


def material_changes(previous: dict, successor: dict) -> list[str]:
    before, after = material_projection(previous), material_projection(successor)
    return sorted(name for name in before if before[name] != after.get(name))


def freshness_now(snapshot: dict, now: Optional[datetime] = None) -> dict:
    """Per-field freshness against the READER's clock, for `presentation`.

    Never written into a snapshot file: the snapshot records what was observed,
    the reader records what that is worth now.
    """
    moment = now or utc_now()
    report: dict[str, dict] = {}

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            if {"value", "observed_at", "ttl_s", "fresh_until", "source"} <= set(node):
                observed = parse_rfc3339(str(node["observed_at"]))
                until = parse_rfc3339(str(node["fresh_until"]))
                report[path] = {
                    "fresh": moment <= until,
                    "age_s": int((moment - observed).total_seconds()),
                    "fresh_until": node["fresh_until"],
                }
                return
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else str(key))

    for key in ("door", "hosts", "gpus", "rungs", "models", "reachability", "trial_runway"):
        walk(snapshot.get(key), key)
    for key in ("leases", "holds"):
        walk(snapshot.get(key), key)
    return dict(sorted(report.items()))


def snapshot_path(snapshot_id: str) -> Path:
    return paths.snapshots_dir() / f"{snapshot_id}.json"


def write_snapshot(snapshot: dict) -> Path:
    """Write the snapshot under its identity, once.

    If the file already exists its bytes are left exactly as they are: a
    snapshot is immutable, and an identical identity means identical content.
    """
    target = snapshot_path(str(snapshot["snapshot_id"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return target
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False) + "\n"
    target.write_text(payload, encoding="utf-8", newline="")
    return target


def load_snapshot(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def catalog_reference(catalog: dict) -> dict:
    return {"catalog_version": str(catalog["catalog_version"]),
            "path": paths.repo_relative(paths.CATALOG_PATH)}


def write_current(catalog: dict, snapshot: dict) -> Path:
    """CURRENT.json holds references only — never an authority, never a caller."""
    target = paths.current_path()
    document = {
        "contract_version": "operator-current.v1",
        "catalog": catalog_reference(catalog),
        "capacity_snapshot": {
            "snapshot_id": str(snapshot["snapshot_id"]),
            "kind": str(snapshot["kind"]),
            "path": paths.repo_relative(snapshot_path(str(snapshot["snapshot_id"]))),
            "observed_at": str(snapshot["observed_at"]),
            "planning_valid_until": str(snapshot["planning_valid_until"]),
        },
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8", newline="")
    return target


def read_current(now: Optional[datetime] = None, *, require_valid: bool = True) -> dict:
    """Read CURRENT.json and the snapshot it names, refusing rather than guessing."""
    current = paths.current_path()
    if not current.is_file():
        raise InspectError(
            f"no current inspection: {current} is missing. `inspect` never captures on its "
            "own — run `operator inspect --refresh` to observe capacity.")
    try:
        document = json.loads(current.read_text(encoding="utf-8"))
        reference = document["capacity_snapshot"]
        snapshot_id = str(reference["snapshot_id"])
        observed_at = parse_rfc3339(str(reference["observed_at"]))
        valid_until = parse_rfc3339(str(reference["planning_valid_until"]))
        catalog_ref = document["catalog"]
    except Exception as exc:  # noqa: BLE001
        raise InspectError(
            f"corrupt CURRENT.json at {current} ({type(exc).__name__}: {exc}). "
            "Run `operator inspect --refresh` to write a new one.") from exc

    target = snapshot_path(snapshot_id)
    if not target.is_file():
        raise InspectError(
            f"CURRENT.json names snapshot {snapshot_id}, but {target} is missing. "
            "Run `operator inspect --refresh`.")
    try:
        snapshot = load_snapshot(target)
    except ValueError as exc:
        raise InspectError(f"corrupt snapshot file {target} ({exc}).") from exc
    if str(snapshot.get("snapshot_id")) != snapshot_id:
        raise InspectError(
            f"snapshot file {target} declares {snapshot.get('snapshot_id')}, but CURRENT.json "
            f"names {snapshot_id}. Refusing to report an identity that does not match its file.")

    moment = now or utc_now()
    if require_valid and moment > valid_until:
        raise InspectError(
            f"the planning window has passed: snapshot {snapshot_id} was observed at "
            f"{rfc3339(observed_at)} and was valid until {rfc3339(valid_until)}; it is now "
            f"{rfc3339(moment)} ({int((moment - valid_until).total_seconds())}s past). "
            "Run `operator inspect --refresh` to observe capacity again — this command "
            "never captures on its own.")
    return {"catalog": catalog_ref, "reference": reference, "snapshot": snapshot,
            "path": target, "observed_at": observed_at, "planning_valid_until": valid_until}


def build_bundle(catalog_ref: dict, snapshot: dict, authority: dict,
                 now: Optional[datetime] = None) -> dict:
    """The inspection bundle: shared truth, this caller's authority, and a
    reader-side freshness reading that never touches the snapshot's bytes."""
    moment = now or utc_now()
    valid_until = parse_rfc3339(str(snapshot["planning_valid_until"]))
    return {
        "contract_version": BUNDLE_CONTRACT_VERSION,
        "catalog": {"catalog_version": str(catalog_ref["catalog_version"]),
                    "path": str(catalog_ref["path"])},
        "capacity_snapshot": {
            "snapshot_id": str(snapshot["snapshot_id"]),
            "kind": str(snapshot["kind"]),
            "path": paths.repo_relative(snapshot_path(str(snapshot["snapshot_id"]))),
            "observed_at": str(snapshot["observed_at"]),
            "planning_valid_until": str(snapshot["planning_valid_until"]),
            "document": snapshot,
        },
        "authority": authority,
        "presentation": {
            "generated_at": rfc3339(moment),
            "planning_window_remaining_s": int((valid_until - moment).total_seconds()),
            "freshness_now": freshness_now(snapshot, moment),
        },
    }


def write_bundle(bundle: dict, caller_id: str) -> Path:
    """Per-caller bundles are noncanonical and live off to one side: this is the
    only operator location that names a caller."""
    safe = "".join(ch if (ch.isalnum() or ch in "-_.") else "_" for ch in caller_id) or "_anonymous"
    target = paths.inspect_dir() / safe / "latest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8", newline="")
    return target


def unreachable_door(reason: str) -> DoorCall:
    """Exported for fault injection and tests."""
    return unavailable_door(reason)


def stale_fields(snapshot: dict, now: Optional[datetime] = None) -> Iterable[str]:
    return [path for path, row in freshness_now(snapshot, now).items() if not row["fresh"]]


def gpu_total_vram(catalog: dict) -> dict[str, Optional[str]]:
    """Total (not free) VRAM per card, from the catalog. Kept here so nothing is
    tempted to read a total as an availability."""
    totals: dict[str, Optional[str]] = {}
    for host in catalog.get("hosts", []):
        for gpu in host.get("gpus", []):
            totals[f"{host['id']}:{gpu.get('bdf') or gpu.get('type')}"] = decimal_str(
                gpu.get("vram_gb")) if gpu.get("vram_gb") is not None else None
    return totals

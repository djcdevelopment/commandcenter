"""The core functions. The CLI and the door mounts both call exactly these (D-102).

There is one implementation of "what does inspect return" and one of "who am I",
and the two entry points differ only in how they learn the caller's identity.
That is the whole reason the door tools exist as a thin provider: a control
plane whose CLI and door could disagree would give two cold agents two different
environments while both cited the same identifiers.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from hearth.operator import authority as authority_mod
from hearth.operator import catalog as catalog_mod
from hearth.operator import history, inspection, paths
from hearth.operator.door import DoorCall
from hearth.operator.identity import Identity


def catalog_document(path: Optional[Path] = None) -> dict:
    """The compiled catalog as committed. Never compiled on the fly: `inspect`
    reports what the repository declares, not what this process could derive."""
    target = Path(path) if path else paths.CATALOG_PATH
    if not target.is_file():
        raise inspection.InspectError(
            f"no compiled catalog at {paths.repo_relative(target)}. "
            "Run `python -m hearth.operator catalog` to compile it.")
    return catalog_mod.read_catalog(target)


def compile_and_write(path: Optional[Path] = None,
                      record_history: bool = True) -> dict:
    """Compile the catalog, write it, and append `catalog.compiled` on a change."""
    target = Path(path) if path else paths.CATALOG_PATH
    previous = None
    if target.is_file():
        try:
            previous = str(catalog_mod.read_catalog(target).get("catalog_version"))
        except ValueError:
            previous = "unparseable"
    document = catalog_mod.compile_catalog()
    written = catalog_mod.write_catalog(document, target)
    changed = previous != document["catalog_version"]
    if changed and record_history:
        history.append("catalog.compiled",
                       {"catalog_version": document["catalog_version"],
                        "previous_catalog_version": previous,
                        "source_count": len(document["generated_from"])},
                       refs={"catalog_path": paths.repo_relative(written)})
    return {"document": document, "path": written, "changed": changed,
            "previous_catalog_version": previous}


def refresh(*, door: Optional[DoorCall] = None,
            cli_runner: Optional[Callable[..., dict]] = None,
            local: bool = False, now: Optional[datetime] = None,
            key: Optional[str] = None,
            catalog: Optional[dict] = None) -> dict:
    """Observe capacity once: write an immutable snapshot, move CURRENT.json,
    and record what changed. The only command in G1 that captures."""
    document = catalog if catalog is not None else catalog_document()

    previous_snapshot = None
    try:
        previous_snapshot = inspection.read_current(now=now, require_valid=False)["snapshot"]
    except inspection.InspectError:
        previous_snapshot = None

    snapshot = inspection.capture(document, door=door, cli_runner=cli_runner,
                                  local=local, now=now, key=key)
    path = inspection.write_snapshot(snapshot)
    inspection.write_current(document, snapshot)

    events = [history.append(
        "capacity.observed",
        {"snapshot_id": snapshot["snapshot_id"],
         "catalog_version": snapshot["catalog_version"],
         "kind": snapshot["kind"],
         "observed_at": snapshot["observed_at"],
         "planning_valid_until": snapshot["planning_valid_until"],
         "material_digest": inspection.material_digest(snapshot),
         "local_probe": bool(local)},
        refs={"snapshot_path": paths.repo_relative(path)})]

    changed: list[str] = []
    invalidated: list[dict] = []
    if previous_snapshot is not None and \
            str(previous_snapshot.get("snapshot_id")) != snapshot["snapshot_id"]:
        changed = inspection.material_changes(previous_snapshot, snapshot)
        if changed:
            events.append(history.append(
                "snapshot.invalidated",
                {"snapshot_id": str(previous_snapshot["snapshot_id"]),
                 "successor_snapshot_id": snapshot["snapshot_id"],
                 "changed_fields": changed},
                refs={"successor_path": paths.repo_relative(path)}))
            # G2-C9 literally (WI-G2a): a superseded snapshot invalidates the open
            # decisions that CITED it, per affected proposal. The WI-G2 candidate
            # enforced supersession only indirectly, by refusing a later
            # re-validation, and `invalidate_decision` had no caller at all.
            from hearth.operator import validate as validate_mod

            invalidated = validate_mod.invalidate_superseded_decisions(
                str(previous_snapshot["snapshot_id"]), snapshot["snapshot_id"], changed)
    return {"snapshot": snapshot, "path": path, "events": events,
            "material_changes": changed, "catalog": document,
            "invalidated_decisions": invalidated}


def inspect_bundle(identity: Identity, *, now: Optional[datetime] = None,
                   write: bool = True) -> dict:
    """The inspection bundle for one caller, from the CURRENT planning snapshot.

    Never captures. A missing, corrupt, mismatched or out-of-window
    CURRENT.json raises InspectError with the reason and the remedy.
    """
    current = inspection.read_current(now=now)
    snapshot = current["snapshot"]
    catalog_ref = current["catalog"]
    granted = authority_mod.evaluate(identity, str(catalog_ref["catalog_version"]))
    bundle = inspection.build_bundle(catalog_ref, snapshot, granted, now=now)
    if write:
        try:
            inspection.write_bundle(bundle, identity.caller_id)
        except OSError:
            # A bundle that cannot be cached is not a failed inspection: the
            # answer is already in hand, and the cache is a convenience.
            pass
    return bundle


def whoami_document(identity: Identity, *, now: Optional[datetime] = None) -> dict:
    """Who this caller is and what it may do, without needing a snapshot.

    Needs the compiled catalog, because an authority evaluated against nothing
    is not an answer: `evaluated_against.catalog_version` is the whole point of
    saying so. A missing catalog raises rather than inventing a version.
    """
    catalog_version = str(catalog_document()["catalog_version"])
    granted = authority_mod.evaluate(identity, catalog_version)
    return {
        "contract_version": "operator-whoami.v1",
        "caller": dict(identity.caller) if identity.caller else None,
        "identity_source": identity.source,
        "identity_reason": identity.reason,
        "authority": granted,
    }

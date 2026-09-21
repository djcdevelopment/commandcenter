"""`python -m hearth.operator <command>` — the operator control plane CLI.

    operator catalog [--check]
    operator inspect [--json] [--refresh] [--local]
    operator whoami [--json]
    operator verify-ids <file>
    operator task submit <path> [--run-id <id>] [--json]
    operator route draft <run_id> [--mode <normal|test>] [--target <rung>] [--json]
    operator route propose <run_id> <path> [--via-door] [--rung <name>] [--json]
    operator route validate <run_id> <proposal_id> [--refresh-stale] [--json]
    operator approve <run_id> <approval_id> [--decision <approve|reject>] [--json]
    operator revoke <run_id> <approval_id> --reason <text> [--json]
    operator history [run_id] [--verify] [--reconcile-tail] [--json]
    operator replay <run_id> [--check] [--json]
    operator explain <run_id>
    operator learn report [--include-test-mode] [--json]
    operator serve [--port 8795]

Exit codes are part of the contract — a cold agent scripts against them:

    0  the command succeeded; for `route validate`, the verdict is `validated`;
       for `history`, the stream is CLEAN — every event verified, appendable
    1  a refusal with a stated reason (rejected verdict, denied approval,
       CORRUPT history, unavailable door rung). For `history`, corrupt means a
       complete record is malformed, out of order, duplicated, tampered with, or
       foreign: damage inside accepted history, which recovery never rewrites
    2  the caller's invocation was wrong (missing file, nothing to check)
    3  `route validate` only: the verdict is `needs_approval` — "wait for a human"
       is not the same answer as "go", and the WI-G2 candidate exited 0 for both
    4  `history` only: RECOVERY-REQUIRED (degraded). The stream's accepted events
       all verify, but the final record is an interrupted partial write: replay
       projects through the last complete event and append is BLOCKED until
       `history <run_id> --reconcile-tail` moves the damaged bytes aside. The
       WI-G2a candidate printed a warning here and exited 0, so a script that
       checked the exit code was told a history it could not append to was fine.

The approver credential is entered at an interactive prompt with hidden input.
It is never accepted through argv, an environment variable, a repository file, a
configuration file, shell history, or agent-readable storage, and it is never
echoed to stdout, stderr, or a log (D-112 item 2).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from hearth.operator import (
    approve as approve_mod,
    artifacts as artifacts_mod,
    authority as authority_mod,
    canonical,
    core,
    envelope as envelope_mod,
    explain as explain_mod,
    history as history_mod,
    identity as identity_mod,
    inspection,
    paths,
    proposal as proposal_mod,
    replay as replay_mod,
    validate as validate_mod,
)
from hearth.operator.canonical import CanonicalError, verify_document

PROG = "python -m hearth.operator"


def _emit(document: dict) -> None:
    print(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False))


def _cmd_catalog(args: argparse.Namespace) -> int:
    from hearth.operator import catalog as catalog_mod

    if args.check:
        ok, message, _ = catalog_mod.check_catalog()
        print(f"capability-catalog: {message}")
        if not ok:
            print(f"  regenerate with `{PROG} catalog`")
        return 0 if ok else 1
    result = core.compile_and_write()
    document = result["document"]
    print(f"capability-catalog: {document['catalog_version']}")
    print(f"  {paths.repo_relative(result['path'])}")
    print(f"  {len(document['generated_from'])} sources, {len(document['hosts'])} hosts, "
          f"{len(document['rungs'])} rungs, {len(document['models'])} models, "
          f"{len(document['tools'])} tools, {len(document['loops'])} loops")
    if result["changed"]:
        print(f"  catalog_version changed (was {result['previous_catalog_version']}); "
              "catalog.compiled appended to runs/operator/_system/history.ndjson")
    return 0


def _render_inspect(bundle: dict) -> None:
    snapshot = bundle["capacity_snapshot"]
    presentation = bundle["presentation"]
    authority = bundle["authority"]
    caller = authority.get("caller")
    print(f"catalog_version : {bundle['catalog']['catalog_version']}")
    print(f"snapshot_id     : {snapshot['snapshot_id']} ({snapshot['kind']})")
    print(f"observed_at     : {snapshot['observed_at']}  "
          f"planning window closes {snapshot['planning_valid_until']} "
          f"({presentation['planning_window_remaining_s']}s left)")
    document = snapshot["document"]
    door = document["door"]["reachable"]
    print(f"door            : reachable={door['value']}"
          + (f"  ({door.get('reason')})" if door.get("reason") else ""))
    for name, rung in document["rungs"].items():
        ready = rung["ready"]
        residency = rung["residency"]["value"]
        print(f"  rung {name:<14} ready={str(ready['value']):<5} "
              f"resident={','.join(residency) if residency else '-'}"
              + (f"  [{ready.get('reason')}]" if ready.get("reason") else ""))
    stale = [path for path, row in presentation["freshness_now"].items() if not row["fresh"]]
    print(f"stale now       : {len(stale)} field(s)"
          + (f" ({', '.join(stale[:4])}{', ...' if len(stale) > 4 else ''})" if stale else ""))
    print(f"caller          : {caller['id'] + ' / ' + str(caller['profile']) if caller else 'null'}")
    granted = [name for name, row in authority["authorities"].items()
               if row["result"] == "granted"]
    gated = [name for name, row in authority["authorities"].items()
             if row["result"] == "human_required"]
    print(f"authority       : {len(granted)} granted, {len(gated)} human_required, "
          f"{9 - len(granted) - len(gated)} denied")


def _cmd_inspect(args: argparse.Namespace) -> int:
    who = identity_mod.resolve_from_env()
    if args.refresh:
        result = core.refresh(local=args.local, key=identity_mod.presented_key())
        if not args.json:
            print(f"captured {result['snapshot']['snapshot_id']} "
                  f"({paths.repo_relative(result['path'])})")
            if result["material_changes"]:
                print("  material change since the previous snapshot: "
                      + ", ".join(result["material_changes"])
                      + " -> snapshot.invalidated appended")
    try:
        bundle = core.inspect_bundle(who)
    except inspection.InspectError as exc:
        print(f"inspect: {exc}", file=sys.stderr)
        return 1
    if args.json:
        _emit(bundle)
    else:
        _render_inspect(bundle)
    return 0


def _cmd_whoami(args: argparse.Namespace) -> int:
    who = identity_mod.resolve_from_env()
    try:
        document = core.whoami_document(who)
    except inspection.InspectError as exc:
        print(f"whoami: {exc}", file=sys.stderr)
        return 1
    if args.json:
        _emit(document)
        return 0
    caller = document["caller"]
    if caller is None:
        print(f"caller          : null ({document['identity_reason']})")
    else:
        print(f"caller          : {caller['id']}")
        print(f"profile         : {caller['profile']}  "
              f"runner_class={caller['runner_class']}  node={caller['node']}")
    print(f"identity source : {document['identity_source']}")
    authority = document["authority"]
    print(f"policy_version  : {authority['evaluated_against']['policy_version']}")
    print(f"catalog_version : {authority['evaluated_against']['catalog_version']}")
    for name, row in authority["authorities"].items():
        print(f"  {name:<28} {row['result']:<14} {row['reason']}")
    return 0


def _cmd_verify_ids(args: argparse.Namespace) -> int:
    target = Path(args.path)
    if not target.is_file():
        print(f"verify-ids: no such file: {target}", file=sys.stderr)
        return 2
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"verify-ids: {target} is not JSON ({exc})", file=sys.stderr)
        return 2
    try:
        results = verify_document(document)
    except CanonicalError as exc:
        print(f"verify-ids: {target} cannot be canonically encoded: {exc}", file=sys.stderr)
        return 2
    if not results:
        print(f"verify-ids: {target} declares no identity this control plane computes "
              "(nothing checked is not the same as verified)", file=sys.stderr)
        return 2
    bad = [row for row in results if not row["ok"]]
    for row in results:
        mark = "ok  " if row["ok"] else "FAIL"
        print(f"{mark} {row['path']}.{row['field']}")
        if not row["ok"]:
            print(f"       declared {row['declared']}")
            print(f"       computed {row['computed']}")
    if bad:
        print(f"verify-ids: {len(bad)} of {len(results)} identities do not match their "
              "content — the bytes changed after the identity was assigned", file=sys.stderr)
        return 1
    print(f"verify-ids: {len(results)} identities match their content")
    return 0


def _cmd_task_submit(args: argparse.Namespace) -> int:
    target = Path(args.path)
    if not target.is_file():
        print(f"task submit: no such file: {target}", file=sys.stderr)
        return 1
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"task submit: {target} is not JSON: {exc}", file=sys.stderr)
        return 1

    run_id = args.run_id or f"run-{canonical.sha256_hex(canonical.canonical_json(raw))[:12]}"
    try:
        if "envelope_id" in raw:
            envelope = envelope_mod.load_envelope(target)
        else:
            who = identity_mod.resolve_from_env()
            envelope = envelope_mod.build_envelope(
                intent=raw.get("intent", ""),
                acceptance_criteria=raw.get("acceptance_criteria", []),
                inputs=raw.get("inputs", {}),
                classification=raw.get("classification", {}),
                constraints=raw.get("constraints", {}),
                supersedes=raw.get("supersedes"),
                submitted_by=who.caller_id if who.caller else None,
                submission_source="cli",
            )
        # A retained raw prompt never enters the envelope or a history payload:
        # it is redacted, stored content-addressed, and referenced by digest.
        stored_path = envelope_mod.store_envelope(envelope, run_id,
                                                  raw_prompt=raw.get("raw_prompt"))
    except Exception as exc:
        print(f"task submit: failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        _emit({"run_id": run_id, "envelope_id": envelope["envelope_id"], "path": str(stored_path)})
    else:
        print(f"task submitted: run_id={run_id}")
        print(f"  envelope_id: {envelope['envelope_id']}")
        print(f"  stored at  : {paths.repo_relative(stored_path)}")
    return 0


def _cmd_route_draft(args: argparse.Namespace) -> int:
    run_id = args.run_id
    refs_dir = paths.run_refs_dir(run_id)
    env_path = refs_dir / "envelope.json"
    if not env_path.is_file():
        print(f"route draft: no envelope found for {run_id} at {env_path}", file=sys.stderr)
        return 1
    envelope = envelope_mod.load_envelope(env_path)

    try:
        current = inspection.read_current()
        snapshot = current["snapshot"]
        catalog = current["catalog"]
        cat_doc = core.catalog_document()
    except Exception as exc:
        print(f"route draft: capacity/catalog unavailable: {exc}", file=sys.stderr)
        return 1

    who = identity_mod.resolve_from_env()
    caller = who.caller or {}
    orchestrator = {
        "provider": "anthropic" if "claude" in caller.get("id", "") else "openai",
        "model": "frontier",
        "endpoint_or_version": "2026",
        "client": caller.get("id", "orchestrator-cli"),
        "harness": "cli",
        "session": caller.get("id", "orchestrator-session"),
    }
    policy_version = authority_mod.policy_version()

    try:
        proposal = proposal_mod.draft_route(
            envelope=envelope,
            snapshot=snapshot,
            catalog=cat_doc,
            orchestrator=orchestrator,
            policy_version=policy_version,
            mode=args.mode,
            target_rung=args.target,
        )
        stored = proposal_mod.store_proposal(proposal, run_id)
    except Exception as exc:
        print(f"route draft: failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        _emit(proposal)
    else:
        print(f"route drafted: proposal_id={proposal['proposal_id']}")
        print(f"  stored at: {paths.repo_relative(stored)}")
    return 0


def _cmd_route_propose(args: argparse.Namespace) -> int:
    run_id = args.run_id
    target = Path(args.path)
    if not target.is_file():
        print(f"route propose: no such file: {target}", file=sys.stderr)
        return 1
    try:
        proposal = json.loads(target.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"route propose: {target} is not JSON: {exc}", file=sys.stderr)
        return 1

    via_door = None
    if args.via_door or args.rung:
        # D-107: the resolved rung, backend and model identity are recorded, and
        # an unavailable default fails explicitly. No silent fallback.
        try:
            snapshot = inspection.read_current(require_valid=False)["snapshot"]
            via_door = proposal_mod.resolve_door_rung(core.catalog_document(), snapshot,
                                                      args.rung)
        except Exception as exc:
            print(f"route propose --via-door: {exc}", file=sys.stderr)
            return 1

    try:
        stored = proposal_mod.store_proposal(proposal, run_id, via_door=via_door)
    except Exception as exc:
        print(f"route propose: failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        _emit({"proposal": proposal, "via_door": via_door})
    else:
        print(f"route proposed: proposal_id={proposal['proposal_id']}")
        print(f"  stored at: {paths.repo_relative(stored)}")
        if via_door:
            print(f"  via door : rung={via_door['rung']} backend={via_door['backend']} "
                  f"model={via_door['model']} (resolved from {via_door['source']})")
    return 0


def _cmd_route_validate(args: argparse.Namespace) -> int:
    run_id = args.run_id
    proposal_id = args.proposal_id
    refs_dir = paths.run_refs_dir(run_id)
    target = refs_dir / f"proposal_{proposal_id}.json"
    if not target.is_file():
        print(f"route validate: no proposal found at {target}", file=sys.stderr)
        return 1
    try:
        proposal = json.loads(target.read_text(encoding="utf-8"))
        who = identity_mod.resolve_from_env()
        refresh_door = None
        if args.refresh_stale:
            refresh_door = inspection.make_door(identity_mod.presented_key())
        result = validate_mod.validate_proposal(proposal, who, run_id=run_id,
                                                refresh_door=refresh_door)
        stored = validate_mod.store_validation(result, run_id, requester=who)
    except Exception as exc:
        print(f"route validate: failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        _emit(result)
    else:
        print(f"route validation: verdict={result['verdict']}")
        print(f"  validation_id: {result['validation_id']}")
        print(f"  stored at    : {paths.repo_relative(stored)}")
        for row in result["checks"]:
            if row["result"] != "passed":
                print(f"  {row['result']:<14} {row['reason_code']}: {row['reason']}")
                if row["remedy"]:
                    print(f"                 remedy: {row['remedy']}")
    return {"validated": 0, "needs_approval": 3}.get(result["verdict"], 1)


def _cmd_approve(args: argparse.Namespace) -> int:
    run_id = args.run_id
    approval_id = args.approval_id
    decision = args.decision

    key = identity_mod.prompt_for_key()
    if not key:
        print("approve: no approver credential was entered. It is accepted only at this "
              "interactive prompt — never from argv, an environment variable, a file, or "
              "shell history (D-112).", file=sys.stderr)
        return 1

    who = identity_mod.resolve_from_key(key)
    del key
    try:
        record = approve_mod.decide_approval(run_id, approval_id, decision, who,
                                             scope=args.scope)
    except Exception as exc:
        print(f"approve: denied: {exc}", file=sys.stderr)
        return 1

    if args.json:
        _emit(record)
    else:
        print(f"approval decided: approval_id={approval_id} decision={decision} "
              f"by {who.caller_id}")
        print(f"  receipt_id: {record['receipt']['receipt_id']}")
        print(f"  expires   : {record['expires_at']}")
    return 0


def _cmd_revoke(args: argparse.Namespace) -> int:
    key = identity_mod.prompt_for_key("revoker key (input is hidden): ")
    if not key:
        print("revoke: no credential was entered (interactive prompt only, D-112)",
              file=sys.stderr)
        return 1
    who = identity_mod.resolve_from_key(key)
    del key
    try:
        record = approve_mod.revoke_approval(args.run_id, args.approval_id, who, args.reason)
    except Exception as exc:
        print(f"revoke: denied: {exc}", file=sys.stderr)
        return 1
    if args.json:
        _emit(record)
    else:
        print(f"approval revoked: approval_id={args.approval_id} by {who.caller_id}")
        print("  a revoked approval is never restored; request a new one (D-112 item 5)")
    return 0


def _cmd_history(args: argparse.Namespace) -> int:
    run_id = args.run_id
    target = paths.run_history_path(run_id) if run_id else paths.history_path()

    if args.reconcile_tail:
        try:
            report = history_mod.reconcile_tail(target)
        except history_mod.HistoryError as exc:
            print(f"history: {exc}", file=sys.stderr)
            return 1
        print(f"history reconciled: {report['events_kept']} complete events kept "
              f"byte-for-byte; {report['damaged_bytes']} damaged byte(s) moved to "
              f"{report['quarantine_path']}")
        return 0

    try:
        report = history_mod.verify(target, run_id or history_mod.SYSTEM_RUN_ID)
        events = history_mod.read_all(target)
    except history_mod.HistoryError as exc:
        print(f"history: {exc}", file=sys.stderr)
        return 1

    # Three conditions, three exit codes: clean 0, recovery-required 4, corrupt 1
    # (a corrupt stream raised HistoryError above). A damaged tail is not a
    # warning a script can miss.
    damaged_tail = bool(report["truncated_tail"])

    if args.verify:
        print(f"history for {run_id or '_system'}: verified {report['count']} event(s), "
              f"sequences {report['first_sequence']}..{report['last_sequence']}, "
              f"every event_id recomputed")
        if damaged_tail:
            print(f"  RECOVERY-REQUIRED (degraded): the {report['count']} accepted events "
                  "above all verify, but the file ends in an interrupted write. Replay "
                  "projects through the last complete event; APPEND IS BLOCKED until "
                  f"`{PROG} history {run_id or ''} --reconcile-tail` moves the damaged "
                  "bytes aside (accepted history is never rewritten).", file=sys.stderr)
            return 4
        return 0

    if args.json:
        _emit({"events": events, "verification": report})
    else:
        print(f"history for {run_id or '_system'}: {len(events)} events")
        for event in events:
            print(f"  [{event.get('sequence'):<3}] {event.get('timestamp')}  "
                  f"{event.get('event_type'):<22}  {event.get('event_id')[:12]}")
    if damaged_tail:
        print("  RECOVERY-REQUIRED (degraded): interrupted final write; append is blocked "
              f"until `{PROG} history {run_id or ''} --reconcile-tail`", file=sys.stderr)
        return 4
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    run_id = args.run_id
    try:
        state = replay_mod.replay_run(run_id, check=args.check)
    except Exception as exc:
        print(f"replay: failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        _emit(state)
    else:
        print(f"replay complete: run_id={run_id}")
        print(f"  status          : {state['status']}")
        print(f"  events replayed : {state['events_replayed']}")
        print(f"  reconstructable : {state['reconstructable']}")
        print(f"  auditable_only  : {state['auditable_only']}")
    return 0


def _cmd_execute(args: argparse.Namespace) -> int:
    from hearth.operator import execute as execute_mod
    run_id = args.run_id
    try:
        res = execute_mod.execute_run(run_id, timeout_s=args.timeout, endpoint=args.endpoint)
    except execute_mod.ExecutionError as exc:
        print(f"execute: failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        _emit(res["receipt"])
    else:
        receipt = res["receipt"]
        print(f"execute complete: run_id={run_id}")
        print(f"  attempt_id    : {receipt['attempt_id']}")
        print(f"  target        : {receipt['target']} ({receipt['route_kind']})")
        print(f"  endpoint      : {receipt['endpoint']}")
        print(f"  model         : {receipt['model']}")
        print(f"  duration      : {receipt['duration_s']}s")
        print(f"  artifact_sha  : {receipt['artifact_sha256']}")
        print(f"  verification  : {res['verification']['verdict']}")
        print(f"  reconstructable: {res['state']['reconstructable']}")
    return 0


def _cmd_learn_report(args: argparse.Namespace) -> int:
    from . import learn as learn_mod
    report = learn_mod.build_report(include_test_mode=args.include_test_mode)
    json_path, html_path = learn_mod.write_report(report)
    if args.json:
        _emit(report)
    else:
        print(f"learning report: {report['runs_considered']} run(s) considered, "
              f"{len(report['recommendations'])} recommendation(s); applied nothing")
        for rec in report["recommendations"]:
            print(f"  {rec['id']} [{rec['kind']}] {rec['statement']}")
        print(f"  written: {paths.repo_relative(json_path)}, {paths.repo_relative(html_path)}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from . import web as web_mod
    return web_mod.serve(host=args.host, port=args.port)


def _cmd_explain(args: argparse.Namespace) -> int:
    run_id = args.run_id
    provenance = None
    if args.via_door or args.rung:
        try:
            snapshot = inspection.read_current(require_valid=False)["snapshot"]
            provenance = proposal_mod.resolve_door_rung(core.catalog_document(), snapshot,
                                                        args.rung)
        except Exception as exc:
            print(f"explain --via-door: {exc}", file=sys.stderr)
            return 1
    try:
        narrative = explain_mod.explain_run(run_id)
    except Exception as exc:
        print(f"explain: failed: {exc}", file=sys.stderr)
        return 1
    if provenance:
        # D-107: the resolved rung, backend and model identity are recorded on
        # the rendering itself. The explanation stays a projection over recorded
        # state — a door rung is a lane, not a licence to narrate (D-115).
        print(f"<!-- door-assisted explanation: rung={provenance['rung']} "
              f"backend={provenance['backend']} model={provenance['model']} "
              f"resolved from {provenance['source']} -->")
    try:
        print(narrative)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((narrative + "\n").encode("utf-8", errors="replace"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG, description="Local Compute Operator control plane (WI-G2).")
    sub = parser.add_subparsers(dest="command", required=True)

    catalog = sub.add_parser("catalog", help="compile the capability catalog")
    catalog.add_argument("--check", action="store_true",
                         help="exit 1 if the written catalog no longer matches the tree")
    catalog.set_defaults(handler=_cmd_catalog)

    inspect = sub.add_parser("inspect", help="the inspection bundle for this caller")
    inspect.add_argument("--json", action="store_true", help="emit the bundle as JSON")
    inspect.add_argument("--refresh", action="store_true",
                         help="observe capacity now and write a new immutable snapshot")
    inspect.add_argument("--local", action="store_true",
                         help="with --refresh, also probe OMEN hold files (read-only)")
    inspect.set_defaults(handler=_cmd_inspect)

    whoami = sub.add_parser("whoami", help="this caller's identity and authority")
    whoami.add_argument("--json", action="store_true")
    whoami.set_defaults(handler=_cmd_whoami)

    verify = sub.add_parser("verify-ids", help="recompute the identities a document declares")
    verify.add_argument("path")
    verify.set_defaults(handler=_cmd_verify_ids)

    # Task subcommands
    task_parser = sub.add_parser("task", help="task envelope operations")
    task_sub = task_parser.add_subparsers(dest="subcommand", required=True)
    task_submit = task_sub.add_parser("submit", help="submit a task envelope")
    task_submit.add_argument("path", help="path to task envelope JSON")
    task_submit.add_argument("--run-id", help="explicit run ID")
    task_submit.add_argument("--json", action="store_true")
    task_submit.set_defaults(handler=_cmd_task_submit)

    # Route subcommands
    route_parser = sub.add_parser("route", help="route proposal and validation")
    route_sub = route_parser.add_subparsers(dest="subcommand", required=True)

    route_draft = route_sub.add_parser("draft", help="draft a deterministic route proposal")
    route_draft.add_argument("run_id", help="run ID")
    route_draft.add_argument("--mode", choices=["normal", "test"], default="normal")
    route_draft.add_argument("--target", default=None,
                             help="pin a catalog rung (e.g. am4-dense) as the direct-inference target")
    route_draft.add_argument("--json", action="store_true")
    route_draft.set_defaults(handler=_cmd_route_draft)

    route_propose = route_sub.add_parser("propose", help="propose a route")
    route_propose.add_argument("run_id", help="run ID")
    route_propose.add_argument("path", help="proposal JSON file")
    route_propose.add_argument("--via-door", action="store_true",
                               help="record that this proposal was drafted with door "
                                    "assistance, resolving the configured default rung "
                                    "(D-107); an unavailable rung fails explicitly")
    route_propose.add_argument("--rung", help="an authorized door rung other than the "
                                              "configured default (deliberate selection, "
                                              "never a fallback)")
    route_propose.add_argument("--json", action="store_true")
    route_propose.set_defaults(handler=_cmd_route_propose)

    route_validate = route_sub.add_parser("validate", help="validate a route proposal")
    route_validate.add_argument("run_id", help="run ID")
    route_validate.add_argument("proposal_id", help="proposal identity SHA")
    route_validate.add_argument("--refresh-stale", action="store_true",
                                help="re-observe required capacity fields past their "
                                     "fresh_until into a bound validation snapshot (D-106)")
    route_validate.add_argument("--json", action="store_true")
    route_validate.set_defaults(handler=_cmd_route_validate)

    # Approve subcommand. There is deliberately no --key: the credential is
    # entered at an interactive prompt with hidden input (D-112 item 2).
    approve = sub.add_parser("approve", help="record a human approval decision")
    approve.add_argument("run_id", help="run ID")
    approve.add_argument("approval_id", help="approval ID")
    approve.add_argument("--decision", choices=["approve", "reject"], default="approve")
    approve.add_argument("--scope", choices=["one_use"], default="one_use")
    approve.add_argument("--json", action="store_true")
    approve.set_defaults(handler=_cmd_approve)

    revoke = sub.add_parser("revoke", help="revoke an approval (append-only, D-112)")
    revoke.add_argument("run_id", help="run ID")
    revoke.add_argument("approval_id", help="approval ID")
    revoke.add_argument("--reason", required=True, help="why the approval is withdrawn")
    revoke.add_argument("--json", action="store_true")
    revoke.set_defaults(handler=_cmd_revoke)

    # History subcommand
    hist = sub.add_parser("history", help="dump or verify execution history")
    hist.add_argument("run_id", nargs="?", help="run ID (defaults to _system)")
    hist.add_argument("--verify", action="store_true",
                      help="verify contract, sequence continuity and every event_id")
    hist.add_argument("--reconcile-tail", action="store_true",
                      help="move an interrupted final write aside so append can resume; "
                           "accepted history is never rewritten")
    hist.add_argument("--json", action="store_true")
    hist.set_defaults(handler=_cmd_history)

    # Replay subcommand
    rep = sub.add_parser("replay", help="deterministic replay and state reconstruction")
    rep.add_argument("run_id", help="run ID")
    rep.add_argument("--check", action="store_true", help="verify reconstructed state matches RUN-STATE.json")
    rep.add_argument("--json", action="store_true")
    rep.set_defaults(handler=_cmd_replay)

    # Execute subcommand
    exe = sub.add_parser("execute", help="physically execute a validated route proposal")
    exe.add_argument("run_id", help="run ID")
    exe.add_argument("--timeout", type=float, default=60.0, help="request timeout in seconds")
    exe.add_argument("--endpoint", help="override execution endpoint URL")
    exe.add_argument("--json", action="store_true")
    exe.set_defaults(handler=_cmd_execute)

    # Explain subcommand
    learn_parser = sub.add_parser("learn", help="offline learning over accumulated runs (recommends only)")
    learn_sub = learn_parser.add_subparsers(dest="subcommand", required=True)
    learn_report = learn_sub.add_parser("report", help="write knowledge/operator_learning.json and OPERATOR-LEARNING.html")
    learn_report.add_argument("--include-test-mode", action="store_true", help="include test_mode runs (excluded by default, D-113)")
    learn_report.add_argument("--json", action="store_true")
    learn_report.set_defaults(handler=_cmd_learn_report)

    srv = sub.add_parser("serve", help="loopback-only, token-authenticated web projection of runs/operator and CURRENT.json")
    srv.add_argument("--host", default="127.0.0.1", help="loopback only; any other host is refused")
    srv.add_argument("--port", type=int, default=8795)
    srv.set_defaults(handler=_cmd_serve)

    exp = sub.add_parser("explain", help="render narrative run explanation")
    exp.add_argument("run_id", help="run ID")
    exp.add_argument("--via-door", action="store_true",
                     help="record the door rung this explanation was assisted by (D-107)")
    exp.add_argument("--rung", help="an authorized door rung other than the default")
    exp.set_defaults(handler=_cmd_explain)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (inspection.InspectError, CanonicalError, history_mod.HistoryError,
            approve_mod.ApprovalError) as exc:
        # A refusal with a stated reason, never a traceback: exit 1 is the
        # contract, and the reason is what the caller acts on.
        print(f"{args.command}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

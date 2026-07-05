"""commander CLI — issue intent to mechnet from a terminal, no frontier session.

    python -m hearth.commander.cli refine "your idea here"
    python -m hearth.commander.cli refine "..." --rounds 4 --fan
    python -m hearth.commander.cli show <intent_id>

REFINE runs the local author<->critic loop live (round-by-round to stdout), then
persists the full trail under hearth/var/commander/refine/ and prints where.
BUILD / BOTH are deferred (see hearth/commander/__init__.py); this is slice 1.
"""
from __future__ import annotations

import argparse
import sys

from hearth.commander.refine import run_refine
from hearth.toolsurface.commander import persist_refine, refine_result


def _print_round(step: dict) -> None:
    n_ok = sum(1 for rv in step["reviews"] if rv["ok"])
    verdicts = ", ".join(
        f"{rv['model'].split(':')[0]}:{rv['verdict'] or 'ERR'}" for rv in step["reviews"]
    )
    print(f"  round {step['round']}: {n_ok}/{len(step['reviews'])} critics answered "
          f"[{verdicts}]", flush=True)


def _cmd_refine(args: argparse.Namespace) -> int:
    idea = args.idea.strip()
    if not idea:
        print("error: empty idea", file=sys.stderr)
        return 2
    print(f"refining (rounds<={args.rounds}, fan={args.fan}): {idea[:80]}", flush=True)
    result = run_refine(idea, rounds=args.rounds, fan=args.fan, on_round=_print_round)
    stored = persist_refine(result, idea)
    if not result.get("ok"):
        print(f"\nFAILED: {result.get('error')}", file=sys.stderr)
        print(f"(partial trail saved: {stored['path']})", file=sys.stderr)
        return 1
    cost = result["cost"]
    status = "converged" if result["converged"] else f"stopped at round {result['rounds_run']}"
    print(f"\n{status} | {cost['author_calls']} author + {cost['critic_calls']} critic "
          f"calls, {cost['tokens_out']} tok, {cost['duration_ms']}ms")
    print(f"intent_id: {stored['intent_id']}")
    print(f"saved:     {stored['path']}\n")
    print("=== FINAL PROPOSAL ===\n")
    print(result["final"])
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    doc = refine_result(args.intent_id)
    if not doc.get("ok"):
        print(f"error: {doc.get('error')}", file=sys.stderr)
        return 1
    print(f"# {doc['intent_id']}  ({doc.get('created')})")
    print(f"idea: {doc.get('idea')}")
    print(f"rounds_run={doc.get('rounds_run')} converged={doc.get('converged')}\n")
    print("=== FINAL PROPOSAL ===\n")
    print(doc.get("final"))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="commander", description="issue intent to mechnet")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("refine", help="refine & review an idea a bunch of times (local)")
    r.add_argument("idea")
    r.add_argument("--rounds", type=int, default=3)
    r.add_argument("--fan", action="store_true",
                   help="spread each review across several local models (qwen + mixtral)")
    r.set_defaults(func=_cmd_refine)

    s = sub.add_parser("show", help="print a stored refinement by intent_id")
    s.add_argument("intent_id")
    s.set_defaults(func=_cmd_show)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

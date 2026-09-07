"""``python -m hearth.backlog`` — read the backlog, promote an intent, check a brief.

Three subcommands, none of which dispatch anything:

  promote-refine <intent_id> --task-class build --requires <glob> [...]
      Write the ``<intent_id>.promote.json`` sidecar that turns a commander
      refine result into a backlog brief. Idempotent: an identical re-run leaves
      the file byte-identical and says ``unchanged``; different arguments are
      refused unless ``--replace``.

  list [--scope all]
      Per-source counts, what did NOT make it in and why, and the ONE brief
      ``select_next`` would choose. This is the dry-run window on the drain's
      selection — it opens no lease, submits nothing, and writes nothing.

  validate <file.md>
      Parse an authored brief and report its fields, or exit 1 naming the fault.

Every path is an explicit flag defaulting to the ``sources.DEFAULT_*`` constant,
so a caller (or a test) can point the whole CLI at a temp root.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from hearth.backlog import select as select_mod
from hearth.backlog import sources as sources_mod
from hearth.backlog.briefs import parse as parse_brief


def _brief_row(brief) -> dict:
    return {
        "source": brief.source,
        "source_ref": brief.source_ref,
        "slug": brief.slug,
        "title": brief.title,
        "task_class": brief.task_class,
        "requires": list(brief.requires),
        "max_age_s": brief.max_age_s,
        "builders": list(brief.builders) if brief.builders else None,
        "plan_id_hint": brief.plan_id_hint(),
        "body_chars": len(brief.body),
    }


def _cmd_list(args: argparse.Namespace) -> int:
    scans = {
        "authored": sources_mod.authored_source(args.queued_dir),
        "refined": sources_mod.refined_source(args.refine_dir),
        "candidate": sources_mod.candidate_source(args.worth_path, args.results_path),
    }
    try:
        chosen = select_mod.select_next(args.scope, scans)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    report = {
        "scope": args.scope,
        "priority": list(select_mod.PRIORITY),
        "admitted_sources": list(select_mod.SCOPES[args.scope]),
        "counts": {name: len(scan) for name, scan in scans.items()},
        "rejected": {name: [dict(r) for r in scan.rejected] for name, scan in scans.items()},
        "next": _brief_row(chosen) if chosen is not None else None,
        "dispatched": False,
    }
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"scope: {args.scope}  (admits {' > '.join(report['admitted_sources'])})")
    for name in select_mod.PRIORITY:
        scan = scans[name]
        print(f"  {name:<10} {len(scan):>3} brief(s)"
              f"{'' if not scan.rejected else f'   [{len(scan.rejected)} not yielded]'}")
        for row in scan.rejected:
            print(f"               - {row.get('source_ref')}: {row.get('reason')}")
    if chosen is None:
        print("next: (nothing to dispatch)")
    else:
        print(f"next: {chosen.source}/{chosen.source_ref}")
        print(f"      slug={chosen.slug} plan_id_hint={chosen.plan_id_hint()}")
        print(f"      task_class={chosen.task_class} requires={list(chosen.requires)} "
              f"max_age_s={chosen.max_age_s}")
        print(f"      title={chosen.title}")
    print("dispatched: no (this command never dispatches)")
    return 0


def _cmd_promote_refine(args: argparse.Namespace) -> int:
    try:
        result = sources_mod.promote_refine(
            args.intent_id,
            refine_dir=args.refine_dir,
            task_class=args.task_class,
            requires=args.requires,
            max_age_s=args.max_age_s,
            builders=args.builders or None,
            promoted_by=args.promoted_by,
            replace=args.replace,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{result['status']}: {result['path']}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        return 1
    try:
        brief = parse_brief(text, source="authored", source_ref=path.name)
    except ValueError as exc:
        print(f"invalid: {path}: {exc}", file=sys.stderr)
        return 1
    row = _brief_row(brief)
    if args.json:
        print(json.dumps({"valid": True, "path": str(path), **row}, indent=2))
    else:
        print(f"valid: {path}")
        for key, value in row.items():
            print(f"  {key}: {value}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m hearth.backlog",
                                 description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="command", required=True)

    promote = sub.add_parser("promote-refine",
                             help="promote a commander refine result into the backlog")
    promote.add_argument("intent_id")
    promote.add_argument("--task-class", required=True)
    promote.add_argument("--requires", action="append", required=True, metavar="GLOB",
                         help="relative deliverable glob (repeatable)")
    promote.add_argument("--max-age-s", type=int, default=None)
    promote.add_argument("--builders", action="append", default=[], metavar="BUILDER")
    promote.add_argument("--refine-dir", default=str(sources_mod.DEFAULT_REFINE_DIR))
    promote.add_argument("--promoted-by", default="derek")
    promote.add_argument("--replace", action="store_true",
                         help="overwrite an existing sidecar that differs")
    promote.set_defaults(func=_cmd_promote_refine)

    listing = sub.add_parser("list", help="show what select_next would choose (dispatches nothing)")
    listing.add_argument("--scope", default="all", choices=sorted(select_mod.SCOPES))
    listing.add_argument("--queued-dir", default=str(sources_mod.DEFAULT_QUEUED_DIR))
    listing.add_argument("--refine-dir", default=str(sources_mod.DEFAULT_REFINE_DIR))
    listing.add_argument("--worth-path", default=str(sources_mod.DEFAULT_CANDIDATE_WORTH_PATH))
    listing.add_argument("--results-path",
                         default=str(sources_mod.DEFAULT_EXPERIMENT_RESULTS_PATH))
    listing.set_defaults(func=_cmd_list)

    validate = sub.add_parser("validate", help="parse an authored brief file")
    validate.add_argument("path")
    validate.set_defaults(func=_cmd_validate)
    return ap


def main(argv: Optional[list] = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

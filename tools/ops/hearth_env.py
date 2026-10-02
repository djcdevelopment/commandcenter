"""hearth-env: show, set and check the lab's environment (dev | prod). ADR-0053.

    hearth-env show
    hearth-env set dev --reason "R&D lap" [--until 2026-10-02T21:00-07:00] [--by derek]
    hearth-env set prod --reason "end lap"
    hearth-env check            # exit 1 if the live file's knobs differ from its source

`set` renders host/environments/<name>.env plus an authored header into ~/.config/hearth/environment
(0644, atomic) and appends one `hearth_environment.set` row to the HEARTH ledger. The switch is an
authored object like the drain's arm state (ADR-0006): who, why, when, and until when.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from fleet import environment as envmod  # noqa: E402

SOURCES = REPO / "host" / "environments"


def source(name: str) -> dict[str, str]:
    return envmod.parse((SOURCES / f"{name}.env").read_text())


def render(name: str, by: str, reason: str, until: str | None) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    head = [f"# Rendered by hearth-env from host/environments/{name}.env. Do not edit; run hearth-env set.",
            f"HEARTH_ENVIRONMENT={name}", f"HEARTH_ENVIRONMENT_SET_BY={by}", f"HEARTH_ENVIRONMENT_SET_AT={now}",
            "HEARTH_ENVIRONMENT_REASON=" + json.dumps(reason)]
    if until:
        head.append(f"HEARTH_ENVIRONMENT_UNTIL={until}")
    body = (SOURCES / f"{name}.env").read_text()
    return "\n".join(head) + "\n\n" + body


def ledger_row(name: str, by: str, reason: str, until: str | None, previous: str) -> str | None:
    try:
        from hearth.kernel.ledger import Ledger, new_event
        return Ledger().append(new_event(
            {"id": "hearth-env", "runner_class": "human" if by == "derek" else "frontier", "node": "omen"},
            "hearth_environment.set",
            args={"environment": name, "previous": previous, "set_by": by, "reason": reason, "until": until},
            ok=True, outcome=f"set:{name}"))
    except Exception as exc:  # noqa: BLE001 -- the file is the switch; the ledger row is its audit line
        print(f"WARNING: ledger append failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def cmd_show(_args) -> int:
    env = envmod.read_environment()
    prod = source("prod")
    live = {**prod, **env["knobs"]}
    out = {"environment": env["name"], "file": env["path"], "missing": env.get("missing", False),
           "expired": env["expired"], "header": env["header"],
           "differs_from_prod": {k: {"prod": prod.get(k), "live": v} for k, v in live.items() if prod.get(k) != v}}
    print(json.dumps(out, indent=1))
    return 0


def cmd_set(args) -> int:
    if args.until:
        try:
            datetime.fromisoformat(args.until.replace("Z", "+00:00"))
        except ValueError:
            sys.exit(f"--until is not ISO-8601: {args.until}")
    previous = envmod.read_environment()["name"]
    text = render(args.name, args.by, args.reason, args.until)
    target = envmod.ENV_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, target)
    event_id = ledger_row(args.name, args.by, args.reason, args.until, previous)
    print(json.dumps({"environment": args.name, "previous": previous, "file": str(target),
                      "until": args.until, "ledger_event_id": event_id}, indent=1))
    return 0


def check() -> tuple[int, str]:
    env = envmod.read_environment()
    if env.get("missing"):
        return 1, f"MISSING: {env['path']} (run: hearth-env set prod --reason ...)"
    name = env["header"].get("HEARTH_ENVIRONMENT", "?")
    if name not in envmod.NAMES:
        return 1, f"UNKNOWN environment {name!r} in {env['path']}"
    live = {k: v for k, v in envmod.parse(Path(env["path"]).read_text()).items() if k not in envmod.HEADER_KEYS}
    want = source(name)
    if live != want:
        diff = sorted(set(live.items()) ^ set(want.items()))
        return 1, f"DRIFT: {env['path']} != host/environments/{name}.env: {diff}"
    note = " (expired: reads as prod)" if env["expired"] else ""
    return 0, f"OK: environment {name}{note}, knobs match host/environments/{name}.env"


def cmd_check(_args) -> int:
    rc, line = check()
    print(line)
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show").set_defaults(fn=cmd_show)
    s = sub.add_parser("set")
    s.add_argument("name", choices=envmod.NAMES)
    s.add_argument("--reason", required=True)
    s.add_argument("--until", help="ISO-8601; after it the environment reads as prod")
    s.add_argument("--by", required=True,
                   help="who authors the switch: derek, or the agent acting on his instruction (e.g. claude-frontier)")
    s.set_defaults(fn=cmd_set)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

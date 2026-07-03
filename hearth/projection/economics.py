"""Economics summary over the HEARTH ledger.

Pure read, mcp-free. The seed of the knowledge-per-local-hour metric and
the naadam frontier-vs-local scoreboard: per runner_class and per tool,
{calls, ok_rate, total_duration_ms, tokens_in, tokens_out}.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_LEDGER = Path("hearth/var/ledger/events.ndjson")


def _empty_bucket() -> dict:
    return {
        "calls": 0,
        "ok": 0,
        "ok_rate": 0.0,
        "total_duration_ms": 0,
        "tokens_in": 0,
        "tokens_out": 0,
    }


def _add(bucket: dict, event: dict) -> None:
    bucket["calls"] += 1
    if event.get("ok"):
        bucket["ok"] += 1
    bucket["total_duration_ms"] += int(event.get("duration_ms") or 0)
    cost = event.get("cost") or {}
    bucket["tokens_in"] += int(cost.get("tokens_in") or 0)
    bucket["tokens_out"] += int(cost.get("tokens_out") or 0)


def _finalize(bucket: dict) -> dict:
    bucket["ok_rate"] = round(bucket["ok"] / bucket["calls"], 4) if bucket["calls"] else 0.0
    return bucket


def summarize(ledger_path: Path = DEFAULT_LEDGER) -> dict:
    """Summarize the ledger. Returns
    {per_runner_class, per_tool, frontier_vs_local, events, parse_errors}."""
    per_runner_class: dict[str, dict] = {}
    per_tool: dict[str, dict] = {}
    events = 0
    parse_errors = 0

    if ledger_path.exists():
        for raw_line in ledger_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                parse_errors += 1
                continue
            events += 1
            runner_class = (event.get("caller") or {}).get("runner_class") or "unknown"
            tool = event.get("tool") or "unknown"
            _add(per_runner_class.setdefault(runner_class, _empty_bucket()), event)
            _add(per_tool.setdefault(tool, _empty_bucket()), event)

    for bucket in per_runner_class.values():
        _finalize(bucket)
    for bucket in per_tool.values():
        _finalize(bucket)

    frontier_vs_local = {
        "frontier": per_runner_class.get("frontier") or _empty_bucket(),
        "local": per_runner_class.get("local") or _empty_bucket(),
    }

    return {
        "per_runner_class": dict(sorted(per_runner_class.items())),
        "per_tool": dict(sorted(per_tool.items())),
        "frontier_vs_local": frontier_vs_local,
        "events": events,
        "parse_errors": parse_errors,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hearth.projection.economics",
        description="Summarize HEARTH ledger economics (pure read).",
    )
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args(argv[1:])

    print(json.dumps(summarize(args.ledger), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

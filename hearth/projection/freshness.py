"""Freshness guard: refuse to let a stale projection pass for a current one.

The failure this exists to catch actually happened, silently, for 26 days
(2026-07-04 -> 2026-07-30). `capacity.json` and `offload.json` read the
`hearth-event.v1` ledger directly and stayed current to the hour, while every
event-derived projection replayed `runs/**/events.jsonl` -- a corpus that
stopped growing the moment the ledger->corpus bridge
(`hearth.projection.ledger_adapter`) stopped being hand-run. The rebuild
reported `ok: True` every time. Nothing was broken; nothing was fresh either.
A from-zero rebuild cannot notice this by construction: replaying a frozen
corpus faithfully produces a frozen projection, and the staging dir it
validates against starts empty, so `corpus_guard` has nothing to compare.

So the check has to come from outside the replay, and it has to compare against
something that moves. It compares against the LEDGER HEAD, not the wall clock --
consistent with the standing doctrine that 'now' is the newest fact the lab
holds, never the system time (see project_associations.evidence_watermark). That
also keeps the guard deterministic and testable: freeze a ledger fixture and the
verdict is fixed.

Three checks, cheapest and most direct first:

  1. bridge   -- the adapter cursor vs the ledger line count. Unprocessed
                 backlog is the most direct possible statement of this bug:
                 cursor at line 3 of 20,110 would have screamed on day one.
  2. corpus   -- the runs-corpus watermark vs the ledger head. Catches a bridge
                 that runs but writes nothing usable, which the cursor alone
                 would not reveal.
  3. documents-- each knowledge/*.json's own stamped watermark vs the ledger
                 head. Catches a projection that is individually wedged while
                 the corpus around it advances.

Documents that stamp no watermark at all are reported `checked: false` with a
reason rather than counted as fresh -- an unverifiable document must never read
as a passing one. Their freshness is covered transitively by check 2: they are
replayed from the same corpus, so a fresh corpus plus a successful rebuild is
what vouches for them.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Four hours of slack over the six-hour rebuild cadence the timer path runs on:
# a single missed cycle is operational noise, two consecutive misses is a fault.
# Arbitrary-at-birth, recorded per the D18 arbitrary-but-traced rule; revise here.
DEFAULT_MAX_LAG_HOURS = 10.0

# Unprocessed ledger lines tolerated before the bridge is called behind. Small and
# non-zero: a rebuild races calls still landing in the ledger, so exact parity is
# not a reachable steady state.
DEFAULT_MAX_BRIDGE_BACKLOG = 50

DEFAULT_LEDGER = Path("hearth/var/ledger/events.ndjson")
DEFAULT_CURSOR = Path("hearth/var/projection_cursor.json")
DEFAULT_KNOWLEDGE = Path("knowledge")
DEFAULT_SOURCES = (Path("runs"),)


class StaleProjectionError(RuntimeError):
    """Raised when a projection has fallen further behind the ledger than allowed."""


def parse_instant(raw: object) -> datetime | None:
    """Parse any timestamp spelling live in this repo into an aware instant.

    All four spellings are in the corpus today (`.ffffff+00:00`, `.ffffffZ`,
    `+00:00`, `Z`). Comparing PARSED INSTANTS is the point: the lexical string
    max used elsewhere in the pipeline (corpus.py, project_policy._watermark,
    project_associations.evidence_watermark) orders those spellings wrong in
    general, and this guard must not inherit that hazard -- a guard that can be
    fooled by a timestamp format is not a guard.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith(("z", "Z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def ledger_head(ledger_path: Path) -> tuple[datetime | None, int]:
    """Newest `ts` in the hearth ledger, and its line count. Streamed: the live
    ledger is already 15 MB and this runs on every rebuild."""
    head: datetime | None = None
    lines = 0
    if not ledger_path.is_file():
        return None, 0
    with ledger_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            if not raw_line.strip():
                continue
            lines += 1
            try:
                instant = parse_instant(json.loads(raw_line).get("ts"))
            except json.JSONDecodeError:
                continue
            if instant is not None and (head is None or instant > head):
                head = instant
    return head, lines


def corpus_watermark(source_paths: tuple[Path, ...] | list[Path]) -> datetime | None:
    """Newest `timestamp` across every events.jsonl under the given source roots."""
    head: datetime | None = None
    for root in source_paths:
        if not Path(root).exists():
            continue
        for events_file in sorted(Path(root).rglob("events.jsonl")):
            try:
                content = events_file.read_text(encoding="utf-8-sig")
            except OSError:
                continue
            for raw_line in content.splitlines():
                if not raw_line.strip():
                    continue
                try:
                    instant = parse_instant(json.loads(raw_line).get("timestamp"))
                except json.JSONDecodeError:
                    continue
                if instant is not None and (head is None or instant > head):
                    head = instant
    return head


def bridge_backlog(cursor_path: Path, ledger_lines: int) -> tuple[int, int]:
    """(unprocessed_lines, cursor_line). A missing cursor means nothing has ever
    been bridged, so the whole ledger is backlog."""
    cursor_line = 0
    if Path(cursor_path).is_file():
        try:
            cursor_line = int(json.loads(
                Path(cursor_path).read_text(encoding="utf-8")).get("line") or 0)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            cursor_line = 0
    return max(0, ledger_lines - cursor_line), cursor_line


def _lag_hours(head: datetime, watermark: datetime) -> float:
    return round((head - watermark).total_seconds() / 3600.0, 2)


def _document_watermark(document: dict) -> tuple[str | None, datetime | None]:
    """The document's own freshness stamp, whatever it calls it. Returns
    (field_name, instant); (None, None) when the document stamps none."""
    for field_name in sorted(key for key in document if "watermark" in key.lower()):
        instant = parse_instant(document[field_name])
        if instant is not None:
            return field_name, instant
    return None, None


def check_freshness(knowledge_dir: Path = DEFAULT_KNOWLEDGE,
                    ledger_path: Path = DEFAULT_LEDGER,
                    sources: tuple[Path, ...] | list[Path] = DEFAULT_SOURCES,
                    cursor_path: Path = DEFAULT_CURSOR,
                    max_lag_hours: float = DEFAULT_MAX_LAG_HOURS,
                    max_bridge_backlog: int = DEFAULT_MAX_BRIDGE_BACKLOG) -> dict:
    """Compare the bridge, the corpus, and every knowledge document against the
    ledger head. Reports; never raises. `ok` is False iff something is stale."""
    head, ledger_lines = ledger_head(Path(ledger_path))
    report: dict = {
        "ok": True,
        "ledger_path": str(ledger_path),
        "ledger_head": head.isoformat() if head else None,
        "ledger_lines": ledger_lines,
        "max_lag_hours": max_lag_hours,
        "max_bridge_backlog": max_bridge_backlog,
        "checks": [],
        "stale": [],
        "unchecked": [],
    }

    if head is None:
        # No ledger head means no reference instant -- say so loudly rather than
        # return a clean bill of health computed from nothing.
        report["ok"] = False
        report["checks"].append({
            "name": "ledger", "kind": "ledger", "checked": False, "stale": True,
            "note": f"no readable ledger head at {ledger_path}",
        })
        report["stale"].append("ledger")
        return report

    backlog, cursor_line = bridge_backlog(Path(cursor_path), ledger_lines)
    bridge_stale = backlog > max_bridge_backlog
    report["checks"].append({
        "name": "bridge", "kind": "bridge", "checked": True, "stale": bridge_stale,
        "cursor_line": cursor_line, "ledger_lines": ledger_lines, "backlog": backlog,
        "note": (f"{backlog} ledger line(s) never projected into the corpus "
                 f"(cursor at {cursor_line}/{ledger_lines})") if bridge_stale
                else f"bridge current (cursor at {cursor_line}/{ledger_lines})",
    })
    if bridge_stale:
        report["stale"].append("bridge")

    corpus_head = corpus_watermark(sources)
    if corpus_head is None:
        report["checks"].append({
            "name": "corpus", "kind": "corpus", "checked": False, "stale": True,
            "note": "no parseable timestamp in any events.jsonl under the source roots",
        })
        report["stale"].append("corpus")
    else:
        lag = _lag_hours(head, corpus_head)
        stale = lag > max_lag_hours
        report["checks"].append({
            "name": "corpus", "kind": "corpus", "checked": True, "stale": stale,
            "watermark": corpus_head.isoformat(), "lag_hours": lag,
            "note": f"corpus watermark lags ledger head by {lag}h" if stale
                    else f"corpus fresh ({lag}h behind ledger head)",
        })
        if stale:
            report["stale"].append("corpus")

    for path in sorted(Path(knowledge_dir).glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError) as exc:
            report["checks"].append({
                "name": path.name, "kind": "document", "checked": False, "stale": True,
                "note": f"unreadable: {exc}",
            })
            report["stale"].append(path.name)
            continue
        if not isinstance(document, dict):
            continue
        field_name, watermark = _document_watermark(document)
        if watermark is None:
            report["checks"].append({
                "name": path.name, "kind": "document", "checked": False, "stale": False,
                "note": "stamps no watermark; freshness covered by the corpus check",
            })
            report["unchecked"].append(path.name)
            continue
        lag = _lag_hours(head, watermark)
        stale = lag > max_lag_hours
        report["checks"].append({
            "name": path.name, "kind": "document", "checked": True, "stale": stale,
            "watermark_field": field_name, "watermark": watermark.isoformat(),
            "lag_hours": lag,
            "note": f"{field_name} lags ledger head by {lag}h" if stale
                    else f"fresh ({lag}h behind ledger head)",
        })
        if stale:
            report["stale"].append(path.name)

    report["ok"] = not report["stale"]
    return report


def format_report(report: dict) -> str:
    """Human-readable lines for the rebuild/CLI output."""
    lines = [
        f"freshness: {'OK' if report['ok'] else 'STALE'} "
        f"(ledger head {report['ledger_head']}, {report['ledger_lines']} line(s), "
        f"threshold {report['max_lag_hours']}h)"
    ]
    for check in report["checks"]:
        if check["stale"]:
            mark = "STALE"
        elif check["checked"]:
            mark = "ok"
        else:
            mark = "--"
        lines.append(f"  [{mark:>5}] {check['name']}: {check['note']}")
    return "\n".join(lines)


def enforce_freshness(**kwargs) -> dict:
    """check_freshness, but raise StaleProjectionError when something is stale.

    This is the loud path: a stale projection must stop a caller that believes
    it is reading current belief, instead of being served quietly.
    """
    report = check_freshness(**kwargs)
    if not report["ok"]:
        raise StaleProjectionError(
            f"stale projection(s): {', '.join(report['stale'])}\n{format_report(report)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hearth.projection.freshness",
        description="Fail loudly when a projection falls behind the ledger head.",
    )
    parser.add_argument("--knowledge", type=Path, default=DEFAULT_KNOWLEDGE)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--cursor", type=Path, default=DEFAULT_CURSOR)
    parser.add_argument("--sources", nargs="+", type=Path, default=list(DEFAULT_SOURCES))
    parser.add_argument("--max-lag-hours", type=float, default=DEFAULT_MAX_LAG_HOURS)
    parser.add_argument("--max-bridge-backlog", type=int, default=DEFAULT_MAX_BRIDGE_BACKLOG)
    parser.add_argument("--json", action="store_true", help="emit the raw report")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    report = check_freshness(
        knowledge_dir=args.knowledge, ledger_path=args.ledger, sources=args.sources,
        cursor_path=args.cursor, max_lag_hours=args.max_lag_hours,
        max_bridge_backlog=args.max_bridge_backlog)
    print(json.dumps(report, indent=2) if args.json else format_report(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

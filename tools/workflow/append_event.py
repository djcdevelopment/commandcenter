from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.workflow.validate_events import validate_event


def append_event(path: Path, event: dict) -> None:
    validate_event(event)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, separators=(",", ":")) + "\n")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: python -m tools.workflow.append_event <events.jsonl> <event.json>")
        return 2

    events_path = Path(argv[1])
    event = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    append_event(events_path, event)
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

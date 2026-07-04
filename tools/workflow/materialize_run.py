from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.workflow.project_state import project_events, read_events
from tools.workflow.projections import write_board_projection, write_otel_mirror
from tools.workflow.validate_events import validate_file
from tools.workflow.fsio import atomic_write_json


def materialize_run(run_dir: Path) -> dict:
    events_path = run_dir / "events.jsonl"
    state_path = run_dir / "state.json"
    board_path = run_dir / "board.json"
    otel_path = run_dir / "otel-events.jsonl"

    errors = validate_file(events_path)
    if errors:
        raise ValueError("\n".join(errors))

    events = read_events(events_path)
    state = project_events(events)
    atomic_write_json(state_path, state)
    write_board_projection(board_path, state)
    write_otel_mirror(otel_path, events)
    return state


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m tools.workflow.materialize_run <run_dir>")
        return 2

    state = materialize_run(Path(argv[1]))
    print(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

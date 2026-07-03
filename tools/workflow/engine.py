from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from tools.workflow.project_capacity import collect_event_files, materialize_knowledge
from tools.workflow.reference_runner import SCENARIOS, run_reference_workflow


def process_work_item(work_item: Path, runs_root: Path, archive: bool, scenario: str,
                      policy_path: Path | None = None,
                      capabilities_path: Path | None = None) -> dict:
    state = run_reference_workflow(work_item, runs_root, scenario, policy_path=policy_path,
                                   capabilities_path=capabilities_path)

    if archive:
        archive_dir = runs_root / work_item.stem / "artifacts" / "inbox"
        archive_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(work_item), archive_dir / work_item.name)

    return state


def process_inbox(inbox_dir: Path, runs_root: Path, archive: bool, scenario: str,
                  policy_path: Path | None = None,
                  capabilities_path: Path | None = None) -> list[dict]:
    results: list[dict] = []
    for work_item in sorted(inbox_dir.glob("*.md")):
        results.append(process_work_item(work_item, runs_root, archive=archive, scenario=scenario,
                                         policy_path=policy_path, capabilities_path=capabilities_path))
    if results:
        materialize_knowledge(collect_event_files([runs_root]), runs_root / "knowledge")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inbox", default="inbox", help="Directory containing *.md work items")
    parser.add_argument("--runs", default="runs", help="Directory for materialized run directories")
    parser.add_argument("--scenario", choices=tuple(SCENARIOS), default="happy")
    parser.add_argument("--policy", default=None, help="Materialized policy.json the scheduler must consult")
    parser.add_argument("--capabilities", default=None,
                        help="Materialized capabilities.json for capability-directed dispatch")
    parser.add_argument("--no-archive", action="store_true", help="Do not move processed inbox items into the run directory")
    parser.add_argument("--once", action="store_true", help="Process current inbox contents once")
    args = parser.parse_args(argv)

    inbox_dir = Path(args.inbox)
    runs_root = Path(args.runs)
    inbox_dir.mkdir(parents=True, exist_ok=True)
    runs_root.mkdir(parents=True, exist_ok=True)

    results = process_inbox(inbox_dir, runs_root, archive=not args.no_archive, scenario=args.scenario,
                            policy_path=Path(args.policy) if args.policy else None,
                            capabilities_path=Path(args.capabilities) if args.capabilities else None)
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

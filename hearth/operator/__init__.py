"""The Local Compute Operator control plane (WI-G1).

`C:\\work\\commandcenter` is a self-describing control plane: a cold agent with
no conversation history enters it, reads `START-HERE.md`, and can answer what
capabilities exist, what capacity is available now, and what it itself may do.
This package is how those answers are produced — once, so the CLI and the door
cannot drift apart.

    python -m hearth.operator catalog [--check]
    python -m hearth.operator inspect [--json] [--refresh] [--local]
    python -m hearth.operator whoami [--json]
    python -m hearth.operator verify-ids <file>

It lives under `hearth/` rather than at the repository root on purpose (D-102):
a top-level `operator` package would shadow Python's standard library `operator`
module for everything that runs with the repository root on `sys.path`.
`operator.cmd` at the root is a shim that forwards here and holds no logic.

Modules:
    canonical    canonical JSON and content-derived identities (one rule, one place)
    catalog      compile the caller-neutral capability catalog
    inspection   capture, read and render immutable capacity snapshots
    authority    evaluate the nine authorities for one caller
    identity     resolve who is asking, on either side of the door
    core         the functions the CLI and the door mounts both call
    envelope     TaskEnvelope normalization, hashing, and storage
    proposal     route proposal structuring, step declarations, bounds
    validate     pure route validation against capacity, catalog, and policy
    approve      approval boundary enforcement under D-112
    history      the append-only operator system and run history
    replay       deterministic event replayer and state reconstruction
    explain      run explanation generator against rubric
    artifacts    artifact reference tracking, durability, and ingestion policy
    door         calling the live door as a data source
    paths        where sources are read and state is written
"""

from __future__ import annotations

__all__ = ["main", "execute_run"]


def execute_run(*args, **kwargs):
    from hearth.operator.execute import execute_run as _exec
    return _exec(*args, **kwargs)


def main(argv=None) -> int:
    """Entry point shared by `__main__` and any in-process caller."""
    from hearth.operator.cli import main as cli_main

    return cli_main(argv)

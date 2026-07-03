"""HEARTH tool provider: build & test digests (Stream H-B).

Coarse tools that collapse frontier round-trips: run a whole suite, return ONLY the
failures — never full stdout. Sandboxed to HEARTH_SCOPE.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from typing import Callable

from hearth.toolsurface._scope import resolve_in_scope, scope_root

_SEPARATOR = re.compile(r"^={10,}$", re.MULTILINE)
_DASHES = re.compile(r"^-{10,}$")
_RAN_LINE = re.compile(r"^Ran (\d+) tests? in ([\d.]+)s$", re.MULTILINE)
_TAIL_RE = re.compile(r"^(OK|FAILED)\s*(\((?P<detail>[^)]*)\))?", re.MULTILINE)
_TRACEBACK_TAIL_LINES = 15
_MAX_LINT_ISSUES = 50


def _failure_blocks(output: str) -> list[dict]:
    """Parse unittest's FAIL:/ERROR: blocks into {id, short_traceback} entries."""
    blocks = []
    for chunk in _SEPARATOR.split(output):
        chunk = chunk.strip("\n")
        if not chunk.startswith(("FAIL:", "ERROR:")):
            continue
        lines = chunk.splitlines()
        header = lines[0]
        traceback_lines: list[str] = []
        in_body = False
        for line in lines[1:]:
            if _DASHES.match(line.strip()):
                if in_body:
                    break  # closing dashed rule (before the "Ran N tests" trailer)
                in_body = True
                continue
            if in_body:
                traceback_lines.append(line)
        blocks.append(
            {
                "id": header.split(":", 1)[1].strip(),
                "kind": "error" if header.startswith("ERROR:") else "failure",
                "short_traceback": "\n".join(traceback_lines[-_TRACEBACK_TAIL_LINES:]).strip(),
            }
        )
    return blocks


def _summary_counts(output: str) -> tuple[int, int, int]:
    """(ran, failures, errors) from unittest's trailer lines."""
    ran_match = _RAN_LINE.search(output)
    ran = int(ran_match.group(1)) if ran_match else 0
    failures = errors = 0
    tail = _TAIL_RE.search(output)
    if tail and tail.group(1) == "FAILED" and tail.group("detail"):
        for part in tail.group("detail").split(","):
            key, _, value = part.strip().partition("=")
            if key == "failures":
                failures = int(value)
            elif key == "errors":
                errors = int(value)
    return ran, failures, errors


def run_tests(suite: str = "tests", runner: str = "unittest", timeout_s: int = 600) -> dict:
    """Run a unittest suite inside the sandbox and return a failures-only digest."""
    if runner != "unittest":
        raise ValueError(f"unsupported runner: {runner!r} (only 'unittest' is wired)")
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    root = scope_root()
    suite_dir = resolve_in_scope(suite)
    if not suite_dir.is_dir():
        raise ValueError(f"suite directory does not exist: {suite}")

    started = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", suite],
            cwd=str(root), capture_output=True, text=True, timeout=timeout_s, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ran": 0, "failures": 0, "errors": 0, "ok": False, "failing_tests": [],
                "duration_s": round(time.monotonic() - started, 2),
                "error": f"suite timed out after {timeout_s}s"}
    duration_s = round(time.monotonic() - started, 2)

    output = proc.stderr + "\n" + proc.stdout  # unittest reports on stderr
    ran, failures, errors = _summary_counts(output)
    failing = _failure_blocks(output)
    digest = {
        "ran": ran,
        "failures": failures,
        "errors": errors,
        "ok": proc.returncode == 0,
        "failing_tests": failing,
        "duration_s": duration_s,
    }
    if ran == 0 and proc.returncode != 0:
        # discovery itself blew up — surface the tail, not the full dump
        digest["error"] = "\n".join(output.strip().splitlines()[-_TRACEBACK_TAIL_LINES:])
    return digest


def lint_digest(paths: list[str] | None = None) -> dict:
    """Lint sandbox paths with ruff or flake8 if one is on PATH; digest, not full output."""
    targets = [str(resolve_in_scope(raw)) for raw in (paths or ["."])]
    linter = next((name for name in ("ruff", "flake8") if shutil.which(name)), None)
    if linter is None:
        return {"available": False, "reason": "neither ruff nor flake8 found on PATH"}
    args = [linter, "check", *targets] if linter == "ruff" else [linter, *targets]
    proc = subprocess.run(args, cwd=str(scope_root()), capture_output=True, text=True,
                          timeout=120, check=False)
    lines = [line for line in (proc.stdout + proc.stderr).splitlines() if line.strip()]
    return {
        "available": True,
        "linter": linter,
        "clean": proc.returncode == 0,
        "issue_count": len(lines) if proc.returncode != 0 else 0,
        "issues": lines[:_MAX_LINT_ISSUES],
        "truncated": len(lines) > _MAX_LINT_ISSUES,
    }


def get_tools() -> list[Callable]:
    return [run_tests, lint_digest]

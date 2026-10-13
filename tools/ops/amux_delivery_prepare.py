#!/usr/bin/env python3
"""amux_delivery_prepare — turn an Amux delivery spec into `submit_local_work` arguments.

Preparation only. This script reads a pinned repository through git, validates the brief with the
delivery contract, and prints the JSON argument object a native HEARTH-connected session passes to
`submit_local_work`. It does not dispatch, choose a lane, seat, backend or model, speak MCP, read
route/qualification state, or record a verdict. Whether the door can take the request (route,
exact-profile qualification, capacity) is the door's answer to the submission and is never
second-guessed or repaired here.

Spec (`amux-delivery-spec.v1`, JSON):
    {"schema": "amux-delivery-spec.v1", "name": "log-dedupe-review", "intent": "[goal:G-...] ...",
     "repo": "/abs/path/to/git/worktree", "base_commit": "<40 hex>", "files": ["rel/path"],
     "task_family": "code_review", "attempt": "r1",            # attempt optional: a new measurement
     "brief": { brief.v2 object; `sources` omitted or equal to files@base_commit }}

Usage:
    amux_delivery_prepare.py SPEC.json [--one-call-qualification] [--out FILE]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hearth.delivery import contract, sourcemap  # noqa: E402

SPEC_SCHEMA = "amux-delivery-spec.v1"
SPEC_KEYS = {"schema", "name", "intent", "repo", "base_commit", "files", "task_family", "brief"}
OPTIONAL_KEYS = {"attempt"}
# Families the door does not treat as delivery review work on a text lane.
REFUSED_FAMILIES = {"vision", "document_ocr", "image_analysis", "code_fix"}
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_GOAL = re.compile(r"^\[goal:G-[A-Za-z0-9._-]+\] \S")


class PrepareError(ValueError):
    """A spec the preparer refuses. Nothing is repaired or defaulted around it."""


def _git(repo: Path, *args: str) -> bytes:
    try:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise PrepareError(f"git {' '.join(args)}: timed out after {exc.timeout}s") from exc
    if done.returncode:
        raise PrepareError(f"git {' '.join(args)}: {done.stderr.decode('utf-8', 'replace').strip()}")
    return done.stdout


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _relative(path: Any) -> str:
    if not isinstance(path, str) or not path or "\\" in path:
        raise PrepareError(f"file path must be a non-empty forward-slash string: {path!r}")
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or path.startswith("./") or p.as_posix() != path:
        raise PrepareError(f"file path must be normalized and relative: {path!r}")
    return path


def _min_budget(words_max: int) -> int:
    """The door's own word-derived default (1.75 tokens per word + 1,536 overhead, floor 2,048)."""
    return max(2048, math.ceil(1.75 * words_max) + 1536)


def _check_brief(spec: dict[str, Any], files: list[str], base: str) -> dict[str, Any]:
    brief = spec["brief"]
    if not isinstance(brief, dict):
        raise PrepareError("brief: must be a brief.v2 object")
    expected = [{"path": name, "commit": base} for name in files]
    if "sources" in brief and brief["sources"] != expected:
        raise PrepareError(f"brief.sources must be omitted or equal to files at base_commit: {expected}")
    brief = {**brief, "sources": expected}
    if "items" in brief:
        raise PrepareError("brief.items is not supported: the items procedure needs a singleton deep route")
    errs = contract.validate_brief(brief)
    if errs:
        raise PrepareError("brief refused: " + "; ".join(errs))
    words = (brief.get("form") or {}).get("words")
    budget = (brief.get("generation") or {}).get("max_tokens")
    if not isinstance(words, dict) or budget is None:
        raise PrepareError("brief needs form.words.max and generation.max_tokens: the report must be bounded")
    if budget < _min_budget(words["max"]):
        raise PrepareError(f"brief.generation.max_tokens {budget} is below {_min_budget(words['max'])}, the door's "
                           f"own headroom for {words['max']} words with quotes")
    return brief


def prepare(spec: Any, *, one_call_qualification: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (submit_local_work arguments, human summary). Raises PrepareError; never dispatches."""
    if not isinstance(spec, dict):
        raise PrepareError("spec must be a JSON object")
    missing = SPEC_KEYS - set(spec)
    extra = set(spec) - SPEC_KEYS - OPTIONAL_KEYS
    if missing or extra:
        raise PrepareError(f"spec keys: missing {sorted(missing)}, unknown {sorted(extra)}"
                           " (backend, model, lane, procedure and max_tokens are not spec fields)")
    if spec["schema"] != SPEC_SCHEMA:
        raise PrepareError(f"spec.schema must be {SPEC_SCHEMA}")
    for key in ("name", "attempt"):
        if key in spec and not (isinstance(spec[key], str) and _ID.match(spec[key])):
            raise PrepareError(f"spec.{key}: must match {_ID.pattern}")
    if not (isinstance(spec["intent"], str) and _GOAL.match(spec["intent"])):
        raise PrepareError("spec.intent: must start with '[goal:G-...] ' (lab entry rule; never rewritten here)")
    family = spec["task_family"]
    if not (isinstance(family, str) and family.strip()):
        raise PrepareError("spec.task_family: non-empty text required")
    if family in REFUSED_FAMILIES:
        raise PrepareError(f"spec.task_family {spec['task_family']!r}: pooled deliveries are reviews, "
                           f"not {sorted(REFUSED_FAMILIES)}")
    repo = Path(spec["repo"]) if isinstance(spec["repo"], str) else None
    if repo is None or not repo.is_absolute() or not repo.exists():
        raise PrepareError("spec.repo: absolute path of an existing git working tree required")
    _git(repo, "rev-parse", "--git-dir")
    base = spec["base_commit"]
    if not (isinstance(base, str) and _SHA40.match(base)):
        raise PrepareError("spec.base_commit: a full 40-hex commit is required (a ref is not a pin)")
    if _git(repo, "rev-parse", "--verify", f"{base}^{{commit}}").decode().strip() != base:
        raise PrepareError(f"spec.base_commit {base} is not a commit in {repo}")
    files = spec["files"]
    if not isinstance(files, list) or not files:
        raise PrepareError("spec.files: non-empty list required")
    files = [_relative(f) for f in files]
    if len(set(files)) != len(files):
        raise PrepareError("spec.files: distinct paths required")
    try:
        smap = sourcemap.build(str(repo), base, files)   # existence at base + the packet cap
    except sourcemap.SourceMapError as exc:
        raise PrepareError(f"source refused: {exc}") from exc
    except subprocess.TimeoutExpired as exc:   # sourcemap runs its own git subprocess
        raise PrepareError(f"source refused: git timed out after {exc.timeout}s reading {files}") from exc
    brief = _check_brief(spec, files, base)
    criteria = [s["statement"] for s in brief["substance"]]
    args: dict[str, Any] = {
        "intent": spec["intent"],
        "acceptance_criteria": criteria,
        "repo": str(repo),
        "base_commit": base,
        "files": files,
        "artifact_kind": "markdown",
        "task_family": spec["task_family"],
        "brief": brief,
    }
    if one_call_qualification:
        args["procedure"] = "one_call"
    digest = hashlib.sha256(_canonical({**args, "attempt": spec.get("attempt", "")}).encode()).hexdigest()
    args["idempotency_key"] = f"amux-delivery:{spec['name']}:{spec.get('attempt', 'a0')}:{digest[:16]}"
    summary = {
        "name": spec["name"], "base_commit": base, "idempotency_key": args["idempotency_key"],
        "procedure": args.get("procedure", "door-selected"),
        "files": [{"path": f.path, "sha256": f.sha256, "lines": len(f.lines)} for f in smap.files],
        "words_max": brief["form"]["words"]["max"], "generation_max_tokens": brief["generation"]["max_tokens"],
        "criteria": [s["id"] for s in brief["substance"]],
    }
    return args, summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("spec", type=Path)
    ap.add_argument("--one-call-qualification", action="store_true",
                    help="pin procedure one_call for an explicit qualification lap (documented separately)")
    ap.add_argument("--out", type=Path, help="write the arguments here instead of stdout")
    ns = ap.parse_args(argv)
    try:
        args, summary = prepare(json.loads(ns.spec.read_text(encoding="utf-8")),
                                one_call_qualification=ns.one_call_qualification)
    except (PrepareError, ValueError, OSError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(args, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if ns.out:
        ns.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    print("prepared (not submitted): " + json.dumps(summary, sort_keys=True), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

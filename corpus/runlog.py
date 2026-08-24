#!/usr/bin/env python3
"""Create run directories and write run-manifest.v1 records.

Every benchmark in this round writes a manifest before it produces a single
number, and updates it when it finishes. The reason is the failure mode this
corpus exists to prevent: five months of results whose hardware, engine build and
flags have to be reconstructed from prose afterwards, if they can be reconstructed
at all.

A run that fails still gets a manifest with `status: failed` and a recorded
failure mode. A failed 120B load on a 128 GB host is a result.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

CONTRACT_VERSION = "run-manifest.v1"

CORPUS_ROOT = Path(__file__).resolve().parent
RUNS_DIR = CORPUS_ROOT / "runs"
CACHE_DIR = CORPUS_ROOT / ".cache"
MODEL_HASH_CACHE = CACHE_DIR / "model_hashes.json"

# Quant level as it appears in llama.cpp filenames.
_QUANT_RE = re.compile(
    r"(IQ\d[_A-Za-z0-9]*|Q\d[_A-Za-z0-9]*|MXFP\d|BF16|F16|F32)", re.I
)
_PARAMS_RE = re.compile(r"(\d+(?:\.\d+)?)[bB](?![a-zA-Z])")


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def new_run_id(kind: str, tag: str | None = None, when: dt.datetime | None = None) -> str:
    stamp = (when or utc_now()).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{kind}-{stamp}"
    return f"{run_id}-{tag}" if tag else run_id


def run_dir(run_id: str, create: bool = True) -> Path:
    path = RUNS_DIR / run_id
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


# --------------------------------------------------------------------------
# provenance helpers
# --------------------------------------------------------------------------

def git_commit(repo: Path | None = None) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo or CORPUS_ROOT.parent),
            capture_output=True, text=True, timeout=20, check=False,
        )
        commit = out.stdout.strip()
        return commit or None
    except (OSError, subprocess.SubprocessError):
        return None


def _load_hash_cache() -> dict:
    try:
        return json.loads(MODEL_HASH_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_hash_cache(cache: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_HASH_CACHE.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n",
                                encoding="utf-8")


def sha256_file(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def model_facts(path: str | Path, want_hash: bool = True) -> dict:
    """Describe a model file. Hashes are cached on (path, size, mtime).

    denning's REPRODUCE.md requires a model sha256 in every manifest, and it is
    right to: quantisations get re-made, filenames get reused, and a benchmark
    compared against the wrong weights is worse than no benchmark. But hashing
    143 GB of GGUFs on every run is not affordable, hence the cache.
    """
    path = Path(path)
    if not path.exists():
        return {"path": str(path), "size_bytes": 0, "sha256": None,
                "missing": True}

    stat = path.stat()
    facts = {
        "name": path.stem,
        "path": str(path),
        "size_bytes": stat.st_size,
        "sha256": None,
        "quant": None,
        "params": None,
    }

    quant = _QUANT_RE.search(path.stem)
    if quant:
        facts["quant"] = quant.group(1)
    params = _PARAMS_RE.search(path.stem)
    if params:
        facts["params"] = f"{params.group(1)}B"

    if want_hash:
        cache = _load_hash_cache()
        key = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
        if key in cache:
            facts["sha256"] = cache[key]
        else:
            facts["sha256"] = sha256_file(path)
            cache[key] = facts["sha256"]
            _save_hash_cache(cache)

    return facts


def engine_facts(engine_dir: str | Path, name: str = "llama.cpp",
                 backend: str | None = None) -> dict:
    """Identify the engine build.

    llama-bench prints its build commit and number into every result row, so the
    adapter can always recover it. Capturing it here too means a manifest stays
    complete even for a run that produced no rows -- which is exactly the case for
    a run that crashed, i.e. the ones where knowing the build matters most.
    """
    engine_dir = Path(engine_dir)
    facts = {
        "name": name,
        "path": str(engine_dir),
        "build_commit": None,
        "build_number": None,
        "version": None,
        "backend": backend,
    }

    binary = engine_dir / "llama-bench.exe"
    if not binary.exists():
        binary = engine_dir / "llama-bench"
    if not binary.exists():
        return facts

    try:
        out = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True,
            timeout=60, check=False, cwd=str(engine_dir),
        )
        text = (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return facts

    build = re.search(r"build\s*[:=]?\s*(\d+)\s*\(([0-9a-f]{6,})\)", text, re.I)
    if build:
        facts["build_number"] = int(build.group(1))
        facts["build_commit"] = build.group(2)
    else:
        number = re.search(r"build number\s*[:=]\s*(\d+)", text, re.I)
        commit = re.search(r"commit\s*[:=]\s*([0-9a-f]{6,})", text, re.I)
        if number:
            facts["build_number"] = int(number.group(1))
        if commit:
            facts["build_commit"] = commit.group(1)
    facts["version"] = text.strip().splitlines()[0] if text.strip() else None
    return facts


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------

def build_manifest(
    run_id: str,
    kind: str,
    platform: str,
    machine: dict,
    engine: dict,
    *,
    tag: str | None = None,
    devices: dict | None = None,
    models: list[dict] | None = None,
    flags: dict | None = None,
    environment: dict | None = None,
    telemetry: dict | None = None,
    notes: list[str] | None = None,
    operator: str | None = None,
    started_utc: str | None = None,
) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "kind": kind,
        "tag": tag,
        "started_utc": started_utc or utc_now().isoformat(),
        "finished_utc": None,
        "operator": operator,
        "hw_id": machine["hw_id"],
        "machine": machine,
        "platform": platform,
        "engine": engine,
        "devices": devices or {},
        "models": models or [],
        "flags": flags or {},
        "environment": environment or {},
        "raw_paths": [],
        "telemetry": telemetry or {},
        "source_repo_commit": git_commit(),
        "notes": notes or [],
        "status": "running",
        "failure": None,
    }


def manifest_path(run_id: str) -> Path:
    return run_dir(run_id) / "manifest.json"


def write_manifest(manifest: dict) -> Path:
    path = manifest_path(manifest["run_id"])
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def read_manifest(run_id: str) -> dict:
    return json.loads(manifest_path(run_id).read_text(encoding="utf-8"))


def finish_run(run_id: str, status: str = "complete",
               failure: dict | None = None,
               raw_paths: list[str] | None = None,
               notes: list[str] | None = None) -> Path:
    """Close out a run. A failure is recorded, never discarded."""
    manifest = read_manifest(run_id)
    manifest["finished_utc"] = utc_now().isoformat()
    manifest["status"] = status
    if failure:
        manifest["failure"] = failure
    if raw_paths:
        manifest["raw_paths"] = sorted(set(manifest.get("raw_paths", []) + raw_paths))
    if notes:
        manifest["notes"] = manifest.get("notes", []) + notes
    return write_manifest(manifest)


def discover_raw_paths(run_id: str) -> list[str]:
    """Every file in the run dir except the manifest, relative to the run dir."""
    base = run_dir(run_id, create=False)
    if not base.exists():
        return []
    return sorted(
        str(p.relative_to(base)).replace("\\", "/")
        for p in base.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or close benchmark runs.")
    parser.add_argument("--list", action="store_true", help="list known runs")
    parser.add_argument("--show", help="print one run's manifest")
    parser.add_argument("--finish", help="mark a run complete and index its raw files")
    parser.add_argument("--status", default="complete",
                        choices=["complete", "failed", "aborted"])
    args = parser.parse_args(argv)

    if args.list:
        if not RUNS_DIR.exists():
            print("no runs yet")
            return 0
        for path in sorted(RUNS_DIR.iterdir()):
            if not path.is_dir():
                continue
            mpath = path / "manifest.json"
            if mpath.exists():
                m = json.loads(mpath.read_text(encoding="utf-8"))
                print(f"{m['run_id']:<48} {m.get('status','?'):<9} "
                      f"{m.get('platform','?'):<16} {m.get('hw_id','?')}")
            else:
                print(f"{path.name:<48} {'(no manifest)':<9}")
        return 0

    if args.show:
        print(json.dumps(read_manifest(args.show), indent=2))
        return 0

    if args.finish:
        path = finish_run(args.finish, status=args.status,
                          raw_paths=discover_raw_paths(args.finish))
        print(f"wrote {path}")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

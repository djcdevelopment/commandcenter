#!/usr/bin/env python3
"""host_config.py — Track OMEN host serving stack configuration and report drift.

Manifest of (repo path -> live path) pairs.
Usage:
    python tools/ops/host_config.py --check
        Exits 0 if all tracked files are byte-identical to live host.
        Exits 1 if any file is missing, drifted, or extra in captured directories.
    python tools/ops/host_config.py --diff <name>
        Prints unified diff for matching file pair.
"""
from __future__ import annotations

import argparse
import difflib
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
HOST_DIR = REPO / "host" / "omen-linux"
HOME = Path.home()

# Explicit manifest of (relative_to_host_omen_linux, absolute_live_path)
MANIFEST: list[tuple[str, Path]] = [
    # systemd user units
    ("systemd/omen-vllm@.service", HOME / ".config" / "systemd" / "user" / "omen-vllm@.service"),
    ("systemd/omen-vllm-router.service", HOME / ".config" / "systemd" / "user" / "omen-vllm-router.service"),
    ("systemd/hearth-production.service", HOME / ".config" / "systemd" / "user" / "hearth-production.service"),
    ("systemd/hearth-operator-web.service", HOME / ".config" / "systemd" / "user" / "hearth-operator-web.service"),
    ("systemd/hearth-ops-pages.service", HOME / ".config" / "systemd" / "user" / "hearth-ops-pages.service"),
    ("systemd/bankedfire-drain.service", HOME / ".config" / "systemd" / "user" / "bankedfire-drain.service"),
    ("systemd/bankedfire-drain.timer", HOME / ".config" / "systemd" / "user" / "bankedfire-drain.timer"),
    ("systemd/hearth-morning-report.service", HOME / ".config" / "systemd" / "user" / "hearth-morning-report.service"),
    ("systemd/hearth-morning-report.timer", HOME / ".config" / "systemd" / "user" / "hearth-morning-report.timer"),
    ("systemd/hearth-dashboard-snapshot.service", HOME / ".config" / "systemd" / "user" / "hearth-dashboard-snapshot.service"),
    ("systemd/hearth-dashboard-snapshot.timer", HOME / ".config" / "systemd" / "user" / "hearth-dashboard-snapshot.timer"),
    ("systemd/mechnet-linux-observer.service", HOME / ".config" / "systemd" / "user" / "mechnet-linux-observer.service"),
    ("systemd/mechnet-linux-observer.timer", HOME / ".config" / "systemd" / "user" / "mechnet-linux-observer.timer"),
    ("systemd/omen-offbox-backup.service", HOME / ".config" / "systemd" / "user" / "omen-offbox-backup.service"),
    ("systemd/omen-offbox-backup.timer", HOME / ".config" / "systemd" / "user" / "omen-offbox-backup.timer"),
    ("systemd/omen-perception.service", HOME / ".config" / "systemd" / "user" / "omen-perception.service"),

    # systemd drop-ins
    ("systemd/omen-vllm@0.service.d/max-model-len.conf.retired-20260927",
     HOME / ".config" / "systemd" / "user" / "omen-vllm@0.service.d" / "max-model-len.conf.retired-20260927"),
    ("systemd/omen-vllm@0.service.d/max-num-seqs.conf",
     HOME / ".config" / "systemd" / "user" / "omen-vllm@0.service.d" / "max-num-seqs.conf"),
    ("systemd/omen-vllm@0.service.d/stage0-recipe.conf",
     HOME / ".config" / "systemd" / "user" / "omen-vllm@0.service.d" / "stage0-recipe.conf"),
    ("systemd/omen-vllm@0.service.d/stage2-27b-mtp.conf",
     HOME / ".config" / "systemd" / "user" / "omen-vllm@0.service.d" / "stage2-27b-mtp.conf"),
    ("systemd/omen-vllm@0.service.d/stage2b-batched.conf",
     HOME / ".config" / "systemd" / "user" / "omen-vllm@0.service.d" / "stage2b-batched.conf"),
    ("systemd/omen-vllm@0.service.d/stage2d-prefix-unit.conf",
     HOME / ".config" / "systemd" / "user" / "omen-vllm@0.service.d" / "stage2d-prefix-unit.conf"),
    ("systemd/omen-vllm@0.service.d/stage8-27b-vision.conf",
     HOME / ".config" / "systemd" / "user" / "omen-vllm@0.service.d" / "stage8-27b-vision.conf"),
    ("systemd/omen-vllm@0.service.d/stage4-gemma4.conf.staged",
     HOME / ".config" / "systemd" / "user" / "omen-vllm@0.service.d" / "stage4-gemma4.conf.staged"),
    ("systemd/omen-vllm@0.service.d/stage5-qwen3-32b.conf.staged",
     HOME / ".config" / "systemd" / "user" / "omen-vllm@0.service.d" / "stage5-qwen3-32b.conf.staged"),
    ("systemd/omen-vllm@0.service.d/stage6-devstral.conf.staged",
     HOME / ".config" / "systemd" / "user" / "omen-vllm@0.service.d" / "stage6-devstral.conf.staged"),
    ("systemd/omen-vllm@0.service.d/stage7-devstral2507.conf.staged",
     HOME / ".config" / "systemd" / "user" / "omen-vllm@0.service.d" / "stage7-devstral2507.conf.staged"),
    ("systemd/omen-vllm@0.service.d/stage8-27b-vision.conf.staged",
     HOME / ".config" / "systemd" / "user" / "omen-vllm@0.service.d" / "stage8-27b-vision.conf.staged"),

    ("systemd/omen-vllm@1.service.d/max-model-len.conf",
     HOME / ".config" / "systemd" / "user" / "omen-vllm@1.service.d" / "max-model-len.conf"),
    ("systemd/omen-vllm@1.service.d/max-num-seqs.conf",
     HOME / ".config" / "systemd" / "user" / "omen-vllm@1.service.d" / "max-num-seqs.conf"),
    ("systemd/omen-vllm@1.service.d/stage0-recipe.conf",
     HOME / ".config" / "systemd" / "user" / "omen-vllm@1.service.d" / "stage0-recipe.conf"),
    ("systemd/omen-vllm@1.service.d/stage1-35b-mtp.conf.retired",
     HOME / ".config" / "systemd" / "user" / "omen-vllm@1.service.d" / "stage1-35b-mtp.conf.retired"),

    ("systemd/hearth-production.service.d/linux-routes.conf",
     HOME / ".config" / "systemd" / "user" / "hearth-production.service.d" / "linux-routes.conf"),

    # bin
    ("bin/start-vllm-seat.sh", HOME / "bin" / "start-vllm-seat.sh"),
    ("bin/wait-vllm-seat.sh", HOME / "bin" / "wait-vllm-seat.sh"),
    ("bin/preflight-vllm-seat.sh", HOME / "bin" / "preflight-vllm-seat.sh"),
    ("bin/generate-api-env.py", HOME / "bin" / "generate-api-env.py"),
    ("bin/mount-windows.sh", HOME / "bin" / "mount-windows.sh"),
    ("bin/mount-omen-c-readonly.sh", HOME / "mount-omen-c-readonly.sh"),
    ("bin/mount-omen-e-readonly.sh", HOME / "mount-omen-e-readonly.sh"),
    ("bin/omen-ai-mode", HOME / ".local" / "bin" / "omen-ai-mode"),
    ("bin/mechnet-readiness-probe", HOME / ".local" / "bin" / "mechnet-readiness-probe"),
    ("bin/omen-offbox-backup.sh", HOME / "bin" / "omen-offbox-backup.sh"),
    ("bin/omen-profile", HOME / "bin" / "omen-profile"),
    ("bin/lab-config", HOME / "bin" / "lab-config"),
    ("profiles/seat0-27b-vision.json", HOME / ".config" / "omen-vllm" / "profiles" / "seat0-27b-vision.json"),

    # config
    ("config/haproxy.cfg", HOME / ".config" / "omen-vllm" / "haproxy.cfg"),
    ("config/lab-configurations.toml", HOME / ".config" / "omen-vllm" / "lab-configurations.toml"),

    # hearth-production
    ("hearth-production/backends-linux.toml", HOME / "hearth-production" / "backends-linux.toml"),
    ("hearth-production/routing-families-linux.toml", HOME / "hearth-production" / "routing-families-linux.toml"),
    ("hearth-production/local-work-routes-linux.toml", HOME / "hearth-production" / "local-work-routes-linux.toml"),
]


def active_manifest() -> list[tuple[str, Path]]:
    """Check the one active seat-0 recipe, plus all staged recipes."""
    profile_path = HOME / ".config" / "omen-vllm" / "profile"
    profile = profile_path.read_text().strip() if profile_path.is_file() else "two-lane"
    inactive = ("stage2-27b-mtp.conf" if profile == "seat0-27b-vision"
                else "stage8-27b-vision.conf")
    return [(rel, live) for rel, live in MANIFEST
            if not rel.endswith("/" + inactive)]

# Fully captured live directories where extra files (present in live, absent from repo) are checked
CAPTURED_DIRS: list[tuple[str, Path]] = [
    ("systemd/omen-vllm@0.service.d", HOME / ".config" / "systemd" / "user" / "omen-vllm@0.service.d"),
    ("systemd/omen-vllm@1.service.d", HOME / ".config" / "systemd" / "user" / "omen-vllm@1.service.d"),
    ("systemd/hearth-production.service.d", HOME / ".config" / "systemd" / "user" / "hearth-production.service.d"),
]


def check() -> int:
    errors = 0
    drifted: list[str] = []
    missing_live: list[str] = []
    missing_repo: list[str] = []
    extra_live: list[str] = []

    repo_files = set()
    pairs = active_manifest()
    for rel_path, live_path in pairs:
        repo_path = HOST_DIR / rel_path
        repo_files.add(rel_path)

        if not repo_path.is_file():
            missing_repo.append(f"MISSING (repo): {repo_path}")
            errors += 1
            continue

        if not live_path.is_file():
            missing_live.append(f"MISSING (live): {live_path}")
            errors += 1
            continue

        try:
            repo_bytes = repo_path.read_bytes()
            live_bytes = live_path.read_bytes()
        except OSError as e:
            errors += 1
            drifted.append(f"ERROR reading pair {rel_path} <-> {live_path}: {e}")
            continue

        if repo_bytes != live_bytes:
            drifted.append(f"DRIFT: host/omen-linux/{rel_path} != {live_path}")
            errors += 1

    # Check for extra files in captured directories
    for rel_dir, live_dir in CAPTURED_DIRS:
        if not live_dir.is_dir():
            continue
        for child in live_dir.iterdir():
            if child.is_file():
                rel_child = f"{rel_dir}/{child.name}"
                if rel_child not in repo_files:
                    extra_live.append(f"EXTRA (live directory {live_dir}): {child.name}")
                    errors += 1

    if missing_repo:
        print("\n".join(missing_repo))
    if missing_live:
        print("\n".join(missing_live))
    if extra_live:
        print("\n".join(extra_live))
    if drifted:
        print("\n".join(drifted))

    if errors == 0:
        print(f"OK: {len(pairs)} host config pairs byte-identical; 0 drift, 0 extra.")
        return 0
    else:
        print(f"FAILED: {errors} issue(s) detected.")
        return 1


def diff_pair(name: str) -> int:
    target_pair = None
    for rel_path, live_path in active_manifest():
        if name in (rel_path, live_path.name, str(live_path), f"host/omen-linux/{rel_path}"):
            target_pair = (rel_path, live_path)
            break
        if name in rel_path or name in str(live_path):
            target_pair = (rel_path, live_path)
            break

    if not target_pair:
        print(f"No manifest pair matches '{name}'.", file=sys.stderr)
        return 2

    rel_path, live_path = target_pair
    repo_path = HOST_DIR / rel_path

    if not repo_path.is_file():
        print(f"Repo file missing: {repo_path}", file=sys.stderr)
        return 1
    if not live_path.is_file():
        print(f"Live file missing: {live_path}", file=sys.stderr)
        return 1

    repo_lines = repo_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    live_lines = live_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        repo_lines,
        live_lines,
        fromfile=f"host/omen-linux/{rel_path}",
        tofile=str(live_path),
    ))

    if not diff:
        print(f"Files are identical: host/omen-linux/{rel_path} == {live_path}")
        return 0

    sys.stdout.writelines(diff)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="OMEN host configuration tracking and drift checker")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Check all pairs and exit 0 if byte-identical")
    group.add_argument("--diff", metavar="NAME", help="Show unified diff for a matched pair")
    args = parser.parse_args()

    if args.check:
        return check()
    elif args.diff:
        return diff_pair(args.diff)
    return 0


if __name__ == "__main__":
    sys.exit(main())

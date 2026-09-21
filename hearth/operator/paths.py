"""Where the operator control plane reads its sources and writes its state.

Two resolutions, deliberately separate, because conflating them is how a tool
run from a worktree writes into the repository it was only supposed to read:

  * **Sources** (inventory, backends, registries, schemas) resolve against the
    repository that contains this package. Run the CLI from a worktree and it
    describes that worktree.
  * **Caller registry** resolves the way the gateway resolves it: ``$HEARTH_ROOT``
    if set, else ``<repo>/hearth`` — then ``var/callers.json``, which is the
    ``--callers`` path in hearth/etc/start-hearth-gateway.cmd. ``hearth/var/`` is
    git-ignored, so a worktree has none; pointing ``HEARTH_ROOT`` at the deployed
    hearth root lets the CLI resolve identity from a worktree without copying a
    registry anywhere. The file is opened by hearth.kernel.auth, never by this
    package, and never printed.
  * **State** (CURRENT.json, snapshots, per-caller bundles, system history)
    resolves against ``HEARTH_OPERATOR_HOME`` if set, else the repository root —
    NEVER against ``HEARTH_ROOT``. That is what keeps a worktree run from
    writing into the deployed tree while still reading its registry.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

# <repo>/hearth/operator/paths.py -> <repo>
REPO_ROOT = Path(__file__).resolve().parents[2]

HEARTH_ROOT_ENV = "HEARTH_ROOT"
OPERATOR_HOME_ENV = "HEARTH_OPERATOR_HOME"
# The caller's own door key. Identity and authorization are the caller's; the
# catalog and the snapshot are everyone's. Never logged, never written to a file.
API_KEY_ENV = "HEARTH_API_KEY"

ETC = REPO_ROOT / "hearth" / "etc"
CONTRACTS = REPO_ROOT / "hearth" / "contracts"

OPERATOR_CONFIG_PATH = ETC / "operator.toml"
AUTHORITY_MAP_PATH = ETC / "authority-map.toml"
PROFILES_PATH = ETC / "profiles.toml"
HARNESSES_PATH = ETC / "harnesses.toml"
LOOPS_PATH = ETC / "loops.toml"
DETERMINISTIC_TOOLS_PATH = ETC / "deterministic-tools.toml"
BACKENDS_PATH = ETC / "backends.toml"
OPERATIONS_PATH = ETC / "operations.toml"
ROUTING_FAMILIES_PATH = ETC / "routing-families.toml"
INVENTORY_PATH = REPO_ROOT / "fleet" / "inventory.toml"
CAPABILITIES_PATH = REPO_ROOT / "hearth" / "kernel" / "capabilities.py"
GPU_CATALOG_PATHS = (
    REPO_ROOT / "knowledge" / "omen_catalog.json",
    REPO_ROOT / "knowledge" / "am4_gpu_catalog.json",
    REPO_ROOT / "knowledge" / "fx99_gpu_catalog.json",
)
OFFLOAD_KNOWLEDGE_PATH = REPO_ROOT / "knowledge" / "offload.json"
CATALOG_PATH = REPO_ROOT / "knowledge" / "capability_catalog.json"


def hearth_root() -> Path:
    """The deployed hearth data root, resolved as hearth.kernel.ledger does it."""
    env = os.environ.get(HEARTH_ROOT_ENV)
    return Path(env).resolve() if env else REPO_ROOT / "hearth"


def callers_registry_path() -> Path:
    """The callers registry the gateway loads (`--callers hearth\\var\\callers.json`).

    Returned as a path only. This package never reads, prints, or copies it —
    hearth.kernel.auth opens it, exactly as the gateway does.
    """
    return hearth_root() / "var" / "callers.json"


def operator_home() -> Path:
    """Where operator state is written. Defaults to the repository root."""
    env = os.environ.get(OPERATOR_HOME_ENV)
    return Path(env).resolve() if env else REPO_ROOT


def current_path() -> Path:
    return operator_home() / "CURRENT.json"


def var_dir() -> Path:
    return operator_home() / "hearth" / "var" / "operator"


def snapshots_dir() -> Path:
    return var_dir() / "snapshots"


def inspect_dir() -> Path:
    return var_dir() / "inspect"


def history_path() -> Path:
    return operator_home() / "runs" / "operator" / "_system" / "history.ndjson"


def runs_dir() -> Path:
    return operator_home() / "runs" / "operator"


def run_dir(run_id: str) -> Path:
    return runs_dir() / run_id


def run_history_path(run_id: str) -> Path:
    return run_dir(run_id) / "history.ndjson"


def run_refs_dir(run_id: str) -> Path:
    return run_dir(run_id) / "refs"


def run_artifacts_index_path(run_id: str) -> Path:
    return run_dir(run_id) / "artifacts.json"


def run_state_path(run_id: str) -> Path:
    return run_dir(run_id) / "RUN-STATE.json"


def raw_artifacts_dir() -> Path:
    return var_dir() / "artifacts"


def history_lock_path() -> Path:
    """Beside the snapshots, not beside the history: the lock is private state,
    and hearth/var/operator/ is already git-ignored."""
    return var_dir() / "history.lock"


def repo_relative(path: Path) -> str:
    """A source path as the catalog records it: repository-relative, forward
    slashes, so the same tree compiles to the same bytes from any checkout."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def load_toml(path: Path) -> dict[str, Any]:
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def operator_config() -> dict[str, Any]:
    return load_toml(OPERATOR_CONFIG_PATH)


def planning_validity_s() -> int:
    return int(operator_config()["snapshot"]["planning_validity_s"])


def field_ttls() -> dict[str, int]:
    return {name: int(value)
            for name, value in operator_config()["snapshot"]["ttl_s"].items()}

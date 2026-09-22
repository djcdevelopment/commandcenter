"""Compile the capability catalog: what exists, with no wall clock and no caller.

`python -m hearth.operator catalog` reads the authoritative sources listed in
``generated_from[]``, hashes each one, and writes
``knowledge/capability_catalog.json``. Two runs over an unchanged tree produce
byte-identical output and the same ``catalog_version``; ``--check`` exits 1 when
the written file no longer matches the tree, on the ``tools/adr_index.py``
pattern.

Three rules do most of the work here:

  * **No wall clock.** Nothing in the catalog says "now". A catalog that changes
    because time passed cannot be cited by two agents as the same environment.
  * **No invented measurement.** A model gets a measurement row only where a
    structured source measured it, carried with its ``sample_count`` and the
    path it came from. No source, no row — never an estimate.
  * **No caller.** No key, no token, no identity, no profile. The catalog is the
    same document for everyone; only ``authority.v1`` differs per caller.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

from hearth.operator import paths
from hearth.operator.canonical import (SOURCE_DIGEST_RULE, canonical_text, decimal_str,
                                       identity_of, source_sha256)

GENERATOR_VERSION = "hearth.operator.catalog/1.0.0"
CONTRACT_VERSION = "capability-catalog.v1"

# Every file whose bytes can change the catalog. Hashed into generated_from[],
# so a source edit moves catalog_version and `catalog --check` goes red.
SOURCES: tuple[Path, ...] = (
    paths.INVENTORY_PATH,
    paths.BACKENDS_PATH,
    paths.OPERATIONS_PATH,
    paths.CAPABILITIES_PATH,
    paths.HARNESSES_PATH,
    paths.LOOPS_PATH,
    paths.DETERMINISTIC_TOOLS_PATH,
    paths.OPERATOR_CONFIG_PATH,
    *paths.GPU_CATALOG_PATHS,
)


def _load_json(path: Path) -> dict:
    with open(path, "rb") as handle:
        return json.load(handle)


def _hosts(inventory: dict) -> list[dict]:
    hosts = []
    for node in inventory.get("node", []):
        gpus = []
        for gpu in node.get("gpus", []) or []:
            gpus.append({
                "type": str(gpu.get("type", "")),
                "bdf": gpu.get("bdf"),
                "vram_gb": decimal_str(gpu.get("vram_gb")),
                "uuid": gpu.get("uuid"),
            })
        gpus.sort(key=lambda row: (str(row["type"]), str(row["bdf"])))
        hosts.append({
            "id": str(node.get("name", "")),
            "kind": str(node.get("kind", "")),
            "address": node.get("address"),
            # Absent means schedulable: fleet/inventory.toml documents the default,
            # and `expect` is fleet_ping's alarm flag, never an availability claim.
            "schedulable": bool(node.get("schedulable", True)),
            "purpose": str(node.get("purpose", "")),
            "gpus": gpus,
        })
    hosts.sort(key=lambda row: row["id"])
    return hosts


def _rungs(backends: dict) -> list[dict]:
    rungs = []
    for backend in backends.get("backend", []):
        settings = backend.get("settings", {}) or {}
        by_model = settings.get("context_bytes_by_model", {}) or {}
        tags = [str(tag) for tag in backend.get("tags", []) or []]
        rungs.append({
            "id": str(backend.get("name", "")),
            "endpoint": str(backend.get("endpoint", "")),
            "api": str(backend.get("api", "")),
            "models": [str(model) for model in backend.get("models", []) or []],
            "tags": tags,
            # An empty tag list means PIN-ONLY, not unavailable: the router will
            # not choose it opportunistically, a caller naming it still gets it.
            "pin_only": not tags,
            "retired": bool(backend.get("retired", False)),
            "context_bytes": _int_or_none(settings.get("context_bytes")),
            "context_bytes_by_model": {str(name): int(value)
                                       for name, value in sorted(by_model.items())},
            "parallel_slots": _int_or_none(settings.get("parallel_slots")),
            "lifecycle": settings.get("lifecycle"),
            "cost_class": settings.get("cost_class"),
            "node": settings.get("node"),
        })
    rungs.sort(key=lambda row: row["id"])
    return rungs


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _models(rungs: list[dict], gpu_catalogs: Iterable[tuple[Path, dict]]) -> list[dict]:
    """One row per model id, with every structured measurement that names it.

    Rows are keyed by model id rather than by (host, model) so a model served on
    two hosts is one capability with two measurements, which is what an
    orchestrator is actually asking about.
    """
    rows: dict[str, dict] = {}

    def row(model_id: str) -> dict:
        return rows.setdefault(model_id, {
            "id": model_id, "served_by": [], "resident_on": [],
            "loadable_on": [], "measurements": [],
        })

    for rung in rungs:
        for model_id in rung["models"]:
            row(model_id)["served_by"].append(rung["id"])

    for path, catalog in gpu_catalogs:
        host = str(catalog.get("host", ""))
        profile = str(catalog.get("hardware_profile_id", ""))
        source = paths.repo_relative(path)
        for resident in catalog.get("resident_models", []) or []:
            row(str(resident))["resident_on"].append(host)
        for model in catalog.get("models", []) or []:
            model_id = str(model.get("model_id", ""))
            entry = row(model_id)
            entry["loadable_on"].append(host)
            entry["measurements"].append({
                "host": host,
                "hardware_profile_id": profile,
                "placement": model.get("placement"),
                "vram_gb": decimal_str(model.get("vram_gb")),
                "per_card_gb": decimal_str(model.get("per_card_gb")),
                "expected_gen_tps": decimal_str(model.get("expected_gen_tps")),
                "sample_count": int(model.get("sample_count", 0)),
                "source": source,
            })

    catalog_rows = []
    for entry in rows.values():
        entry["served_by"] = sorted(set(entry["served_by"]))
        entry["resident_on"] = sorted(set(entry["resident_on"]))
        entry["loadable_on"] = sorted(set(entry["loadable_on"]))
        entry["measurements"].sort(key=lambda row: (row["host"], row["source"]))
        catalog_rows.append(entry)
    catalog_rows.sort(key=lambda row: row["id"])
    return catalog_rows


def _operations(operations: dict) -> list[dict]:
    rows = []
    for name, raw in (operations.get("operation", {}) or {}).items():
        rows.append({
            "id": str(name),
            "description": str(raw.get("description", "")),
            "handler": str(raw.get("handler", "")),
            "default_model": raw.get("default_model"),
            "max_prompt_bytes": _int_or_none(raw.get("max_prompt_bytes")),
            "max_tokens_ceiling": _int_or_none(raw.get("max_tokens_ceiling")),
            "deadline_ceiling_s": _int_or_none(raw.get("deadline_ceiling_s")),
        })
    rows.sort(key=lambda row: row["id"])
    return rows


def _tools() -> list[dict]:
    # Imported here, not at module import: the door provider mounts these
    # functions, and nothing in this package should require the kernel merely to
    # be imported.
    from hearth.kernel.capabilities import TOOL_CAPABILITY

    rows = [{"id": name, "capability": capability}
            for name, capability in TOOL_CAPABILITY.items()]
    rows.sort(key=lambda row: row["id"])
    return rows


def _harnesses(registry: dict) -> list[dict]:
    rows = []
    for entry in registry.get("harness", []):
        rows.append({
            "id": str(entry.get("id", "")),
            "kind": str(entry.get("kind", "")),
            "status": str(entry.get("status", "")),
            "repository": entry.get("repository"),
            "commit": entry.get("commit"),
            "entrypoint": entry.get("entrypoint"),
            "subagents": sorted(str(name) for name in entry.get("subagents", []) or []),
            "last_evidence": str(entry.get("last_evidence", "")),
            "notes": entry.get("notes"),
        })
    rows.sort(key=lambda row: row["id"])
    return rows


def _loops(registry: dict) -> list[dict]:
    rows = []
    for entry in registry.get("loop", []):
        implementations = []
        for impl in entry.get("implementation", []) or []:
            implementations.append({
                "id": str(impl.get("id", "")),
                "status": str(impl.get("status", "")),
                "entrypoint": impl.get("entrypoint"),
                "last_evidence": str(impl.get("last_evidence", "")),
                "notes": impl.get("notes"),
            })
        implementations.sort(key=lambda row: row["id"])
        rows.append({
            "id": str(entry.get("id", "")),
            "description": str(entry.get("description", "")),
            # The lane's own status, so "review is ABSENT" is a stated fact and
            # not something a reader has to infer from the absence of a LIVE row.
            "status": str(entry.get("status", "")),
            "implementations": implementations,
        })
    rows.sort(key=lambda row: row["id"])
    return rows


def _deterministic_tools(registry: dict) -> list[dict]:
    rows = []
    for entry in registry.get("tool", []):
        rows.append({
            "id": str(entry.get("id", "")),
            "kind": str(entry.get("kind", "")),
            "invocation": str(entry.get("invocation", "")),
            "json_output": bool(entry.get("json_output", False)),
            "status": str(entry.get("status", "")),
            "last_evidence": str(entry.get("last_evidence", "")),
            "notes": entry.get("notes"),
        })
    rows.sort(key=lambda row: row["id"])
    return rows


def _policies(config: dict) -> tuple[dict, dict, list[dict]]:
    catalog_policy = config.get("catalog", {}) or {}
    worktree_policy = dict(catalog_policy.get("worktree_policy", {}) or {})
    test_policy = dict(catalog_policy.get("test_policy", {}) or {})
    known = [dict(row) for row in test_policy.get("known_failures", []) or []]
    known.sort(key=lambda row: (str(row.get("path", "")), int(row.get("line", 0))))
    if known:
        test_policy["known_failures"] = known
    systems = []
    for entry in catalog_policy.get("artifact_systems", []) or []:
        systems.append({
            "id": str(entry.get("id", "")),
            "path": str(entry.get("path", "")),
            "kind": str(entry.get("kind", "")),
            "git_tracked": bool(entry.get("git_tracked", False)),
            "description": str(entry.get("description", "")),
        })
    systems.sort(key=lambda row: row["id"])
    return worktree_policy, test_policy, systems


def compile_catalog(sources: Optional[Iterable[Path]] = None) -> dict:
    """Build the catalog document, identity included. Pure: reads files, writes none.

    `sources` defaults to SOURCES at CALL time, not at definition time, so the
    source list is one fact with one place to change it.
    """
    source_paths = list(sources if sources is not None else SOURCES)
    generated_from = [{"path": paths.repo_relative(path), "sha256": source_sha256(path)}
                      for path in source_paths]
    generated_from.sort(key=lambda row: row["path"])

    inventory = paths.load_toml(paths.INVENTORY_PATH)
    backends = paths.load_toml(paths.BACKENDS_PATH)
    operations = paths.load_toml(paths.OPERATIONS_PATH)
    harnesses = paths.load_toml(paths.HARNESSES_PATH)
    loops = paths.load_toml(paths.LOOPS_PATH)
    deterministic = paths.load_toml(paths.DETERMINISTIC_TOOLS_PATH)
    config = paths.load_toml(paths.OPERATOR_CONFIG_PATH)
    gpu_catalogs = [(path, _load_json(path)) for path in paths.GPU_CATALOG_PATHS]

    rungs = _rungs(backends)
    worktree_policy, test_policy, artifact_systems = _policies(config)

    document = {
        "contract_version": CONTRACT_VERSION,
        "generator_version": GENERATOR_VERSION,
        "source_digest_rule": SOURCE_DIGEST_RULE,
        "generated_from": generated_from,
        "hosts": _hosts(inventory),
        "rungs": rungs,
        "models": _models(rungs, gpu_catalogs),
        "operations": _operations(operations),
        "tools": _tools(),
        "harnesses": _harnesses(harnesses),
        "loops": _loops(loops),
        "deterministic_tools": _deterministic_tools(deterministic),
        "worktree_policy": worktree_policy,
        "test_policy": test_policy,
        "artifact_systems": artifact_systems,
    }
    document["catalog_version"] = identity_of(document, "catalog_version")
    return document


def render(document: dict) -> str:
    """The exact file bytes: canonical JSON plus one trailing newline.

    The file is canonical so that reading it back and re-hashing needs no
    normalization step, and a diff of two catalogs is a diff of their content.
    """
    return canonical_text(document) + "\n"


def write_catalog(document: dict, path: Path | None = None) -> Path:
    target = Path(path) if path else paths.CATALOG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(document), encoding="utf-8", newline="")
    return target


def read_catalog(path: Path | None = None) -> dict:
    target = Path(path) if path else paths.CATALOG_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def check_catalog(path: Path | None = None) -> tuple[bool, str, dict]:
    """Compare the written catalog with a fresh compilation of the tree."""
    target = Path(path) if path else paths.CATALOG_PATH
    document = compile_catalog()
    expected = render(document)
    if not target.is_file():
        return False, f"missing: {paths.repo_relative(target)}", document
    # Newline-normalized: git checks this file out CRLF on Windows and LF on a
    # fleet node, and a checkout translation is not a drift in the catalog.
    actual = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    if actual != expected:
        try:
            written = json.loads(actual).get("catalog_version", "unreadable")
        except ValueError:
            written = "unparseable"
        return False, (f"STALE: {paths.repo_relative(target)} carries {written}, "
                       f"the tree compiles to {document['catalog_version']}"), document
    return True, f"current ({document['catalog_version']})", document

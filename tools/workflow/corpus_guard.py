"""Corpus regression guard (Stream A2).

The rot test protects derived knowledge files from being hand-edited. NOTHING protected them
from a re-projection that sees LESS evidence than the previous run — which is exactly the
2026-07-02 incident, where a pipeline rerun over an incomplete corpus clobbered
knowledge/*.json and the first real capability was lost from the derived store.

`guard_write` refuses a projection write whose evidence watermark is OLDER, or whose primary
count is SMALLER, than the file already on disk — unless an authored Clause-2 override permits
the regression (with a reason, on an audit trail). Diff-clean reruns (equal watermark, equal
count) pass untouched, so D18 determinism is never disturbed.

This changes only WHETHER a projector may overwrite a file, never WHAT it computes.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Tuple

# Authored override + audit + batch-progress bookkeeping all live in the SAME directory as the
# file being written (path.parent), never a hardcoded knowledge/ path — tests run against temp
# knowledge dirs, and each projector may target a different --out.
OVERRIDE_FILE = "corpus_regression_override.json"
AUDIT_FILE = "policy_audit.ndjson"
PROGRESS_FILE = ".corpus_regression_progress.json"

# Stream A4 (approved D-A3-5): the repo root this module lives in. Injectable in
# check_fixture_taint so tests can simulate a repo-knowledge target inside a temp sandbox.
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_COMPONENT = "fixtures"
ALLOW_FIXTURE_FLAG = "--allow-fixture-sources"

# An extractor maps a document to (watermark_or_None, primary_count_or_None).
Extractor = Callable[[dict], Tuple[Optional[str], Optional[int]]]


class CorpusRegressionError(RuntimeError):
    """Raised when a projection would overwrite a file with less-evidenced output."""


class FixtureTaintError(RuntimeError):
    """Raised when a projection would pour fixture sources into the repo's own knowledge/ store."""


def check_fixture_taint(source_paths, out_dir, *, allow: bool = False,
                        repo_root: Optional[Path] = None) -> None:
    """Fixture-taint guard (Stream A4, approved D-A3-5).

    The 2026-07-02 incident chain began when fixtures/workflow/runs was poured through the
    projectors into the repo's own knowledge/ store; every downstream loss traces to that.
    This check refuses exactly that combination and nothing else: it raises FixtureTaintError
    when BOTH (a) any resolved source path contains a path component exactly equal to
    "fixtures", AND (b) the resolved out dir IS the repo's own knowledge dir (repo root
    derived from this module's location, injectable for tests). Tests projecting fixtures
    into temp dirs pass untouched; real-corpus projections into knowledge/ pass untouched.

    Escape hatch (authored act, Clause-2 shape): `allow=True` — wired to the projectors'
    --allow-fixture-sources flag — permits the write AND appends an audit record (sources,
    out dir, flag used) to the same policy_audit.ndjson append trail the A2 override path
    uses. The audit record carries no wall-clock timestamp (D18).
    """
    root = Path(repo_root).resolve() if repo_root is not None else REPO_ROOT
    repo_knowledge_dir = (root / "knowledge").resolve()
    resolved_out = Path(out_dir).resolve()
    if resolved_out != repo_knowledge_dir:
        return

    resolved_sources = [Path(source).resolve() for source in source_paths]
    tainted = [source for source in resolved_sources if FIXTURE_COMPONENT in source.parts]
    if not tainted:
        return

    if not allow:
        raise FixtureTaintError(
            f"fixture taint blocked: source {tainted[0]} lies under a {FIXTURE_COMPONENT!r} "
            f"path component while --out targets the repo's own knowledge store "
            f"({resolved_out}). Rule A4/D-A3-5: projectors must never project fixture "
            f"sources into repo knowledge/ — that is the incident that destroyed the belief "
            f"store. If this is a deliberate authored act, re-run with {ALLOW_FIXTURE_FLAG}; "
            f"the permit will be audited to {AUDIT_FILE}."
        )

    record = {
        "event": "fixture_taint_permitted",
        "sources": [str(source) for source in resolved_sources],
        "tainted_sources": [str(source) for source in tainted],
        "out_dir": str(resolved_out),
        "flag": ALLOW_FIXTURE_FLAG,
    }
    resolved_out.mkdir(parents=True, exist_ok=True)
    with (resolved_out / AUDIT_FILE).open("a", encoding="utf-8") as audit:
        audit.write(json.dumps(record) + "\n")


def make_extractor(count_key: Optional[str],
                   watermark_key: str = "evidence_watermark") -> Extractor:
    """Build the per-file extractor. Files that lack a watermark or a count simply resolve
    that side to None, and the corresponding comparison is skipped (see the rules below).

    Primary count per knowledge file (Stream A2):
      findings.json / coverage.json / capacity_estimates.json / prediction_accuracy.json
                                       -> observation_count
      associations.json               -> association_count
      capabilities.json               -> capability_count
      experiment_candidates.json / policy.json -> source_findings
      experiment_results.json         -> plan_count
      known_good/bad_models.json      -> neither watermark nor count -> UNGUARDED (see
                                         DECISION-NEEDED-A2.md).
    """
    def extract(doc: dict) -> Tuple[Optional[str], Optional[int]]:
        watermark = doc.get(watermark_key)
        count = doc.get(count_key) if count_key else None
        return watermark, count
    return extract


def _parse_ts(value: str) -> datetime:
    # Stored evidence watermarks are ISO-8601 UTC (…Z). Parsing a stored timestamp is
    # deterministic — this is not a wall-clock read.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _watermark_regressed(old_wm: Optional[str], new_wm: Optional[str]) -> bool:
    # Compare only when BOTH docs carry a watermark.
    if old_wm is None or new_wm is None:
        return False
    return _parse_ts(new_wm) < _parse_ts(old_wm)


def _count_regressed(old_count: Optional[int], new_count: Optional[int]) -> bool:
    # Compare only when BOTH docs carry a count.
    if old_count is None or new_count is None:
        return False
    return new_count < old_count


def _write(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def _active_override(directory: Path, file_name: str) -> Optional[dict]:
    """The override active for THIS file, or None. Active means the file exists, `active` is
    truthy, and file_name is in its scope."""
    override_path = directory / OVERRIDE_FILE
    if not override_path.is_file():
        return None
    override = json.loads(override_path.read_text(encoding="utf-8"))
    if not override.get("active"):
        return None
    if file_name not in override.get("scope", []):
        return None
    return override


def _audit_permit(directory: Path, file_name: str, override: dict,
                  old_wm, new_wm, old_count, new_count) -> None:
    record = {
        "event": "corpus_regression_permitted",
        "file": file_name,
        "old_watermark": old_wm,
        "new_watermark": new_wm,
        "old_count": old_count,
        "new_count": new_count,
        "reason": override.get("reason"),
        "author": override.get("author"),
    }
    with (directory / AUDIT_FILE).open("a", encoding="utf-8") as audit:
        audit.write(json.dumps(record) + "\n")


def _record_progress(directory: Path, file_name: str, override: dict) -> None:
    """Track which scoped files have been written under the active override. Deactivate the
    override only once EVERY file in its scope has been written — per-batch, not per-file, so a
    multi-file projector (or a multi-step chain) does not strand mid-run with the override
    already spent. Progress is persisted next to the override so it survives across the
    separate projector processes."""
    scope = set(override.get("scope", []))
    progress_path = directory / PROGRESS_FILE
    written = set()
    if progress_path.is_file():
        written = set(json.loads(progress_path.read_text(encoding="utf-8")).get("written", []))
    written.add(file_name)

    if scope <= written:
        override["active"] = False
        _write(directory / OVERRIDE_FILE, override)
        progress_path.unlink(missing_ok=True)
    else:
        _write(progress_path, {"written": sorted(written)})


def guard_write(path: Path, new_doc: dict, extract: Extractor) -> None:
    """Write `new_doc` to `path` as pretty JSON — unless doing so would regress the corpus.

    Regression = the new watermark is older than the on-disk one, OR the new primary count is
    smaller (each compared only when both docs carry that field). Either blocks the write with
    a CorpusRegressionError naming both watermarks and both counts, unless an active authored
    override in `path.parent` lists this file, in which case the write proceeds, the regression
    is audited, and the override is deactivated once its whole scope has been written.
    """
    path = Path(path)
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)

    new_wm, new_count = extract(new_doc)
    override = _active_override(directory, path.name)

    if path.is_file():
        old_wm, old_count = extract(json.loads(path.read_text(encoding="utf-8")))
        if _watermark_regressed(old_wm, new_wm) or _count_regressed(old_count, new_count):
            if override is None:
                raise CorpusRegressionError(
                    f"corpus regression blocked writing {path.name}: watermark "
                    f"{old_wm!r} -> {new_wm!r}, count {old_count!r} -> {new_count!r}. "
                    f"Author an active {OVERRIDE_FILE} in {directory} whose scope includes "
                    f"{path.name!r} to accept this regression."
                )
            _audit_permit(directory, path.name, override, old_wm, new_wm, old_count, new_count)

    _write(path, new_doc)

    # Any scoped file written under an active override counts toward batch completion, whether
    # or not it individually regressed — otherwise a non-regressing scoped file would leave the
    # override permanently active.
    if override is not None:
        _record_progress(directory, path.name, override)

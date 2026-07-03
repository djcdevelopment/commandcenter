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

# An extractor maps a document to (watermark_or_None, primary_count_or_None).
Extractor = Callable[[dict], Tuple[Optional[str], Optional[int]]]


class CorpusRegressionError(RuntimeError):
    """Raised when a projection would overwrite a file with less-evidenced output."""


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

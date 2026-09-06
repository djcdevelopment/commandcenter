"""HEARTH's memory of what it ASKED the fleet for (task-lane expectations sidecar).

Why this file exists (transitional, 2026-09-06). ``masters_pet(apply=True)`` stubs
any run older than ``PHANTOM_AGE_S`` (30 min) that has no ``result.json``, which
makes a deliberate multi-hour build indistinguishable from a dead one. The fix is
for the watchdog to know the lifetime HEARTH asked for — but the conductor does
not (yet) copy the CCMETA header into the run directory, so **during** a run the
only place that expectation exists is HEARTH's own memory. This sidecar is that
memory.

What it is and is NOT:
  * It records **what HEARTH asked for** (``max_age_s``, ``requires``,
    ``task_class``, ``submitted_at``) — never the run's state. Run state belongs
    to the conductor (ADR-0033: the run dir is the run, ``result.json`` is the
    only terminal marker).
  * A value attached to the RUN always wins over this file. See
    ``hearth.health.gaps.apply_expectations``: on disagreement the run-attached
    value is used and ``expectation_conflict`` is set — never a silent pick.
  * It is transitional. The moment the conductor copies ``max_age_s``/``requires``
    from the CCMETA header into ``result.json`` (or a run-dir sidecar), this file
    stops being the source and becomes a redundant cache. That conductor-side
    change is the OPEN entry appended to DECISIONS-PENDING.md on 2026-09-06.

Location: ``$HEARTH_ROOT/var/task_lane/expectations.json`` when HEARTH_ROOT is set,
else ``<repo>/hearth/var/task_lane/expectations.json`` (gitignored via
hearth/.gitignore). That resolution rule is DELIBERATELY DUPLICATED from the
ledger's own root helper rather than imported: the tool surface stays kernel-free
by frozen contract (hearth/tests/toolsurface/test_provider_contract.py asserts no
provider module mentions the kernel package, and a transitive import through this
module would be exactly the laundering route that test exists to prevent).

The path is NOT routed through ``hearth.toolsurface._scope.resolve_in_scope`` on
purpose: per-caller narrowing (ADR-0019) exists to keep ``hearth/var`` OUT of a
research caller's sandbox, so resolving this file in caller scope would either
fail or widen exactly what narrowing protects. It is HEARTH's own bookkeeping,
reached by an explicit path, and every mutation of it happens inside a ledgered
tool call.

Failure discipline (never an exception, never a silent success):
  * missing file            -> ``{}``, no warning
  * unreadable/invalid JSON -> ``{}`` + ``warning``
  * JSON that is not an object -> ``{}`` + ``warning``
  * individual non-dict entries -> dropped + ``warning`` naming how many
  * writes are atomic (temp file + ``os.replace``); a crash between the two
    leaves the previous file byte-intact.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# The repo root this module lives in — same idiom as hearth/toolsurface/_scope.py
# and hearth/health/rungstate.py.
_REPO_ROOT = Path(__file__).resolve().parents[2]
HEARTH_ROOT_ENV = "HEARTH_ROOT"
SIDECAR_RELPATH = ("var", "task_lane", "expectations.json")

# --- Bounds (chosen here, justified here) -------------------------------------
# max_age_s upper bound. 7 days. Rationale: the bound has to be generous enough
# for the longest build anyone would deliberately queue (the plan's first live
# use is 21600 s = 6 h, and a multi-day burn-in campaign is plausible) yet tight
# enough that a typo cannot silently disable the watchdog forever — max_age_s
# raises the phantom threshold, so an unbounded value would mean a dead run is
# never stubbed and holds occupancy indefinitely. 7 days is ~28x the longest
# planned use and still a bound a human notices.
MAX_AGE_S_CAP = 7 * 24 * 3600  # 604800

# Safety-net prune: an entry this old is discarded regardless of anything else,
# so the file cannot grow without limit even if every other prune rule misses.
STALE_ENTRY_MAX_AGE_S = 30 * 24 * 3600  # 2592000

# THERE IS DELIBERATELY NO "no run dir yet, so drop it" RULE, and so no grace
# period to tune. An earlier version of this module pruned an entry once no
# runs/<id>/ dir had appeared within an hour of submit, on the reasoning that the
# conductor's serve loop turns an inbox item into a run dir in ~3 s, so an hour is
# ~1200x the healthy latency. That measures the healthy case and then trusts it as
# a bound. When the serve loop is wedged while cc-conductor still answers SSH, the
# gather SUCCEEDS and shows no run dir — and a missing directory is absence of
# evidence, not evidence that the task is gone. The entry gets pruned, the loop
# recovers, the run finally starts — and it starts stripped of the max_age_s
# HEARTH asked for, so 30 minutes later the watchdog reads it as a phantom and
# masters_pet(apply=True) stubs a live multi-hour build. That is exactly the
# failure this sidecar exists to prevent, re-created by its own housekeeping, and
# the exposure is widest for the long deliberate runs that need the protection
# most. Keeping such entries costs nothing: STALE_ENTRY_MAX_AGE_S already bounds
# the file, and an entry for a plan that never ran is a few hundred bytes
# annotating nothing.

# Bound the requires list so a manifest cannot smuggle an unbounded blob into the
# CCMETA header (which travels base64 over SSH into the conductor's inbox).
MAX_REQUIRES_ITEMS = 64
MAX_REQUIRES_ITEM_CHARS = 512

_ENTRY_KEYS = ("max_age_s", "requires", "task_class", "submitted_at")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")

# Guards the read-modify-write in record_expectation. IN-PROCESS ONLY, stated as
# a limitation rather than implied away: two threads in this process interleave
# safely (test_two_threads_recording_different_plan_ids_both_persist), but two
# separate PROCESSES writing the same sidecar are last-writer-wins — the loser's
# entry is lost. Acceptable today because the gateway is a single always-on
# writer (ADR: "single writer behind one always-on MCP gateway"); it would need a
# real file lock the day a second process writes this file.
_WRITE_LOCK = threading.RLock()


# --- Paths --------------------------------------------------------------------

def hearth_var_root() -> Path:
    """The hearth data root: ``$HEARTH_ROOT`` if set, else ``<repo>/hearth``.

    Deliberately duplicates the ledger's own root rule instead of importing it
    (see the module docstring: providers stay kernel-free by frozen contract).
    Read at CALL time, not import time, so a test can point the whole sidecar at
    a temp root with one env var.
    """
    env = os.environ.get(HEARTH_ROOT_ENV)
    return Path(env).resolve() if env else _REPO_ROOT / "hearth"


def default_expectations_path() -> Path:
    """``$HEARTH_ROOT/var/task_lane/expectations.json`` (gitignored)."""
    return hearth_var_root().joinpath(*SIDECAR_RELPATH)


def _resolve(path) -> Path:
    return Path(path) if path is not None else default_expectations_path()


def utc_now_iso() -> str:
    """ISO-8601 UTC with a Z suffix — the same stamp shape the ledger writes."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# --- Validation (runs BEFORE any SSH write; see task_lane.submit_task) --------

def validate_max_age_s(value: object, where: str = "max_age_s") -> Optional[int]:
    """Return ``value`` as a positive int of seconds, or None when absent.

    Stricter than ``_validate_est_tokens``: floats are rejected outright rather
    than accepted when integral. A lifetime is authored by a human ("six hours"),
    not computed, so a float here means the caller is confused about units — and
    the cost of guessing wrong is a watchdog that stubs a live build or spares a
    dead one. Bools are rejected before the int check (``True`` is an int).
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"{where} must be a positive integer number of seconds "
            f"(1..{MAX_AGE_S_CAP}) when provided")
    if value <= 0:
        raise ValueError(
            f"{where} must be a positive integer number of seconds "
            f"(1..{MAX_AGE_S_CAP}) when provided")
    if value > MAX_AGE_S_CAP:
        raise ValueError(
            f"{where} must be <= {MAX_AGE_S_CAP} s (7 days); got {value}")
    return int(value)


def validate_requires(requires: object, where: str = "requires") -> Optional[list[str]]:
    """Return ``requires`` as a list of relative glob strings, or None when absent.

    These strings ride the CCMETA header to the conductor and will later be
    matched against a run's deliverables (B-02 owns that harvest/assay side).
    Refused here: absolute paths, drive letters, ``..`` segments, NUL/newline,
    empty strings, an empty list, non-strings. The refusal is a ValueError raised
    BEFORE any SSH so a malformed manifest never leaves half a batch on the
    conductor — the same discipline submit_batch already applies to est_tokens.
    """
    if requires is None:
        return None
    if not isinstance(requires, list) or not requires:
        raise ValueError(f"{where} must be a non-empty list of relative glob strings")
    if len(requires) > MAX_REQUIRES_ITEMS:
        raise ValueError(f"{where} must hold at most {MAX_REQUIRES_ITEMS} patterns")
    out: list[str] = []
    for i, item in enumerate(requires):
        label = f"{where}[{i}]"
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label} must be a non-empty string")
        if len(item) > MAX_REQUIRES_ITEM_CHARS:
            raise ValueError(f"{label} must be at most {MAX_REQUIRES_ITEM_CHARS} characters")
        if "\x00" in item or "\n" in item or "\r" in item:
            raise ValueError(f"{label} must not contain NUL or newline characters")
        norm = item.replace("\\", "/")
        if norm.startswith("/"):
            raise ValueError(f"{label} must be a relative path, not absolute")
        if _DRIVE_RE.match(item):
            raise ValueError(f"{label} must not name a drive letter")
        if any(seg == ".." for seg in norm.split("/")):
            raise ValueError(f"{label} must not contain a '..' segment")
        out.append(item)
    return out


# --- Load / save --------------------------------------------------------------

def load_expectations(path=None) -> dict:
    """Read the sidecar. Returns ``{"path", "expectations", "warning"}``.

    NEVER raises and never silently succeeds: a missing file is ``{}`` with
    ``warning: None``; anything unreadable, non-JSON, non-object, or holding
    non-dict entries yields ``{}`` (or the surviving entries) plus a human-readable
    ``warning`` string that the caller is expected to surface — ``masters_pet``
    and ``patrol`` both put it in their ``expectations`` block.
    """
    target = _resolve(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"path": str(target), "expectations": {}, "warning": None}
    except OSError as exc:
        return {"path": str(target), "expectations": {},
                "warning": f"expectations sidecar unreadable: {type(exc).__name__}: {exc}"}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        return {"path": str(target), "expectations": {},
                "warning": f"expectations sidecar is not valid JSON ({exc}); treated as empty"}
    if not isinstance(parsed, dict):
        return {"path": str(target), "expectations": {},
                "warning": (f"expectations sidecar is a {type(parsed).__name__}, expected an "
                            f"object of plan_id -> entry; treated as empty")}
    expectations: dict = {}
    dropped = 0
    for plan_id, entry in parsed.items():
        if isinstance(plan_id, str) and isinstance(entry, dict):
            expectations[plan_id] = entry
        else:
            dropped += 1
    warning = None
    if dropped:
        warning = (f"expectations sidecar had {dropped} malformed entr"
                   f"{'y' if dropped == 1 else 'ies'}; they were ignored")
    return {"path": str(target), "expectations": expectations, "warning": warning}


def save_expectations(expectations: dict, path=None,
                      replace: Optional[Callable[[str, str], None]] = None) -> Path:
    """Write the sidecar atomically: temp file in the same directory, then replace.

    ``os.replace`` is atomic on both Windows and POSIX, so a reader never sees a
    half-written file and a crash between the temp write and the replace leaves
    the PREVIOUS file byte-intact. ``replace`` is injectable (defaulting to
    ``os.replace`` looked up at call time) so a test can inject that crash — the
    same runner-injection idiom ``task_lane._run_ssh`` already uses. On a failed
    replace the temp file is removed best-effort so a failure leaves no litter.
    """
    target = _resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    tmp.write_text(json.dumps(expectations, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    replace_fn = replace or os.replace
    try:
        replace_fn(str(tmp), str(target))
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return target


def record_expectation(plan_id: str, entry: dict, path=None,
                       replace: Optional[Callable[[str, str], None]] = None) -> dict:
    """Remember what HEARTH asked for on one submitted task.

    ``entry`` may carry ``max_age_s``, ``requires``, ``task_class`` and
    ``submitted_at``; unknown keys are dropped and None values are omitted, so the
    file only ever holds the four documented fields. ``submitted_at`` defaults to
    now (UTC, Z-suffixed).

    IDEMPOTENT BY PLAN_ID, LAST WRITE WINS: recording the same plan_id twice keeps
    exactly one entry, the newer one. plan_ids are uuid-suffixed
    (``task_lane._new_plan_id``) so a genuine collision is not a real scenario;
    this rule just makes a retry harmless.

    The read-modify-write is serialized by an in-process lock (see _WRITE_LOCK's
    stated cross-process limitation). Returns
    ``{"path", "plan_id", "entry", "count", "warning"}``.
    """
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise ValueError("plan_id must be a non-empty string")
    if not isinstance(entry, dict):
        raise ValueError("entry must be a dict")
    row = {k: entry[k] for k in _ENTRY_KEYS if entry.get(k) is not None}
    row.setdefault("submitted_at", utc_now_iso())
    target = _resolve(path)
    with _WRITE_LOCK:
        doc = load_expectations(target)
        expectations = dict(doc["expectations"])
        expectations[plan_id] = row
        save_expectations(expectations, target, replace=replace)
        count = len(expectations)
    return {"path": str(target), "plan_id": plan_id, "entry": row,
            "count": count, "warning": doc["warning"]}


# --- Cleanup ------------------------------------------------------------------

def _entry_age_s(entry: dict, now: float):
    """Seconds since ``submitted_at``, or None when it is absent/unparseable."""
    ts = entry.get("submitted_at")
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return now - dt.timestamp()


def prune_expectations(expectations: dict, records, now: Optional[float] = None,
                       max_entry_age_s: int = STALE_ENTRY_MAX_AGE_S) -> tuple[dict, list[dict]]:
    """Pure. Returns ``(kept, pruned)``; ``pruned`` is ``[{plan_id, reason}, ...]``.

    Two rules, in order:
      1. ``finished`` — the run has a ``result.json`` (``has_result``). The
         expectation has done its job; the run is terminal (ADR-0033). This is the
         only rule that prunes on positive EVIDENCE about the run.
      2. ``stale`` — older than ``max_entry_age_s`` (30 days) regardless of
         anything else. Purely a bound on the file's size, deliberately set far
         beyond any lifetime ``max_age_s`` can express (its cap is 7 days), so it
         can never be the rule that ends a run's protection while that run might
         still be alive.
    (``malformed`` — an entry that is not a dict — is dropped on sight; that is a
    shape check, not a rule about a run.)

    Absence of a ``runs/<plan_id>/`` dir in this gather is deliberately NOT a
    reason to prune, at any age — see the block above ``STALE_ENTRY_MAX_AGE_S``.
    A gather that succeeds while the conductor's serve loop is wedged shows no run
    dir for a task that is merely late; discarding the entry there hands the
    eventual run to the watchdog with its declared lifetime removed, which is the
    false phantom this file exists to prevent.

    Neither argument is mutated. An entry whose ``submitted_at`` is missing or
    unparseable has an unknown age, so rule 2 cannot judge it — rule 1 is what
    removes it; that residual is stated rather than papered over.
    """
    now = time.time() if now is None else now
    finished_ids = set()
    for r in records or ():
        if not isinstance(r, dict):
            continue
        pid = r.get("plan_id")
        if not isinstance(pid, str):
            continue
        if r.get("has_result"):
            finished_ids.add(pid)
    kept: dict = {}
    pruned: list[dict] = []
    for plan_id, entry in (expectations or {}).items():
        if not isinstance(entry, dict):
            pruned.append({"plan_id": plan_id, "reason": "malformed"})
            continue
        age = _entry_age_s(entry, now)
        if plan_id in finished_ids:
            pruned.append({"plan_id": plan_id, "reason": "finished"})
            continue
        if age is not None and age > max_entry_age_s:
            pruned.append({"plan_id": plan_id, "reason": "stale"})
            continue
        kept[plan_id] = entry
    return kept, pruned

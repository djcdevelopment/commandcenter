"""Correlation gap-checks — the guard dog's first spellbook (Watchfire/Flare Slice 0).

Pure, IO-free rules over run records. Each rule is a "spell": a named coherence
check that fires when two sources disagree. Kept pure so the rules are
unit-testable without SSH and reusable by both the on-demand `patrol` HEARTH
tool and (later) the scheduled watchdog (Watchfire).

v0 covers the ledger/runs signals that beat us on 2026-07-04 (see
WATCHFIRE-FLARE-DESIGN-2026-07-04.html). The physical-vs-claim correlation
(AM4 GPU util vs a "running" claim — "the fans, digitized") is the next spell;
it needs cross-node telemetry and is deliberately out of this slice.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict

from hearth.health.rungstate import summarize_for_notes

# No result.json this long after dispatch => phantom/stalled run holding occupancy.
PHANTOM_AGE_S = 1800  # 30 minutes

# knowledge_stale (S3): the ledger-native projection older than this means the
# knowledge_rebuild gateway timer (6h cadence) has been failing long enough to
# blow the freshness SLO.
KNOWLEDGE_STALE_AGE_S = 86400  # 24 hours

# schedule_divergence (JS6): actual duration this many times over the bucket's
# p90 counts as "far longer than the capacity envelope predicts". Strictly
# greater-than — exactly 2x is still within the envelope, not a divergence.
SCHEDULE_DIVERGENCE_FACTOR = 2

# Substrings in a builder's surfaced question that mean "my source checkout is stale".
_STALE_CHECKOUT_MARKERS = (
    "does not exist",
    "not found in the repository",
    "not found in",
    "no such file",
    "directory does not exist",
)


@dataclass
class Gap:
    kind: str       # spell name
    severity: str   # "high" | "warn" | "info"
    plan_id: str
    detail: str


def _as_int(v):
    return v if isinstance(v, int) else None


def load_capacity_document(path) -> "dict | None":
    """Read a capacity.v1 document (see hearth/contracts/capacity.v1.schema.json)
    from the HEARTH sandbox. Returns None (not a raise) when the file is absent
    or unreadable/malformed — capacity data is optional evidence, never a hard
    dependency; the schedule_divergence spell just fires nothing without it.
    Import is local to avoid a toolsurface->health import at module load time.
    """
    from hearth.toolsurface._scope import resolve_in_scope

    try:
        resolved = resolve_in_scope(path)
        if not resolved.is_file():
            return None
        return json.loads(resolved.read_text(encoding="utf-8"))
    except (ValueError, OSError, json.JSONDecodeError):
        return None


def _find_bucket(buckets, task_class, node, tool):
    """(task_class, node) -> (tool, node) -> any-node-for-task fallback chain."""
    by_task_node = None
    by_tool_node = None
    by_task_any = None
    for b in buckets:
        if not isinstance(b, dict):
            continue
        b_task = b.get("task_class")
        b_node = b.get("node")
        b_tool = b.get("tool")
        if by_task_node is None and task_class is not None and b_task == task_class and b_node == node:
            by_task_node = b
        if by_tool_node is None and tool is not None and b_tool == tool and b_node == node:
            by_tool_node = b
        if by_task_any is None and task_class is not None and b_task == task_class and b_node is None:
            by_task_any = b
    return by_task_node or by_tool_node or by_task_any


def _bucket_p90(bucket) -> "float | None":
    """Extract p90 (ms) from a bucket's duration_ms, or None.

    A bucket with null p90 means all events were failures and were excluded from
    percentiles — such a bucket carries no valid duration data and should not match."""
    if not isinstance(bucket, dict):
        return None
    duration_ms = bucket.get("duration_ms") or {}
    p90 = duration_ms.get("p90")
    # Only return p90 if it's a valid positive number; null/None/zero/non-numeric
    # values mean the bucket has no valid data.
    if isinstance(p90, (int, float)) and p90 > 0:
        return float(p90)
    return None


def scan_runs(records, phantom_age_s: int = PHANTOM_AGE_S, capacity: "dict | None" = None) -> "list[Gap]":
    """Apply the coherence spells to a list of run records; return the gaps found.

    A record is a plain dict (shape produced by patrol._gather_runs):
    ``plan_id``, ``age_s``, ``has_result``, ``dispatched`` and — when
    ``has_result`` is true —
    ``status``, ``error``, ``stub``, ``winner``, ``winner_grade``,
    ``winner_files``, ``n_questions``, ``questions_text``, ``promoted``.
    """
    gaps: "list[Gap]" = []
    for r in records:
        pid = r.get("plan_id", "?")
        age = r.get("age_s", 0) or 0

        # Spell: phantom_in_flight — claims running, produced nothing for too long.
        # ``dispatched: False`` means the run dir never got a nodes.json: the
        # conductor aborted before pinning a builder graph (its "no build workers
        # available" path mkdirs first and returns), or the dir predates the
        # convention. Same gap either way — it holds occupancy and will never
        # produce a result — so only the wording differs; the heal is identical.
        if not r.get("has_result"):
            if age >= phantom_age_s:
                stage = ("never dispatched (no nodes.json)" if r.get("dispatched") is False
                         else "reads in-flight but is stalled/errored")
                gaps.append(Gap("phantom_in_flight", "warn", pid,
                    f"no result after {age // 60} min — {stage}; holds phantom occupancy"))
            continue

        # Spell: crashed_isolated — a terminal ERROR result. Key on the error
        # status itself, NOT the bare _stub flag: a watchfire heal-stub is also a
        # stub but its status is "abandoned" (resolved), not "errored" — a heal
        # must resolve a gap, never relabel it as a fresh crash.
        err = r.get("error") or ""
        if r.get("status") == "errored" or "errored (isolated)" in err:
            gaps.append(Gap("crashed_isolated", "high", pid,
                f"run errored: {(err or r.get('status') or '').strip()[:120]}"))

        # Spell: stale_checkout — builder reports missing files (stale reference checkout).
        qtext = (r.get("questions_text") or "").lower()
        if any(m in qtext for m in _STALE_CHECKOUT_MARKERS):
            gaps.append(Gap("stale_checkout", "high", pid,
                "builder reports missing files — likely a stale ~/commandcenter-src reference checkout"))
        # Spell: false_success (blocked) — finished/graded but blocked by pending questions.
        elif r.get("n_questions") and not r.get("promoted"):
            gaps.append(Gap("false_success", "warn", pid,
                f"graded {r.get('winner_grade')} but has {r['n_questions']} pending question(s) — "
                f"deliverable likely empty/blocked, not the pass the grade implies"))

        # Spell: false_success (empty) — winner graded but produced ~no files.
        wf = _as_int(r.get("winner_files"))
        if wf is not None and wf <= 1 and r.get("winner_grade"):
            gaps.append(Gap("false_success", "warn", pid,
                f"winner graded {r.get('winner_grade')} but produced {wf} file(s) — likely empty deliverable"))

        # Spell: schedule_divergence (JS6, info, flag-only) — a completed run
        # took far longer than its capacity envelope predicts. Needs both a
        # measurable actual duration AND a matching bucket; either missing
        # means "no evidence either way", not "fine" — so it silently skips.
        buckets = (capacity or {}).get("buckets") or []
        duration_s = r.get("duration_s")
        if buckets and isinstance(duration_s, (int, float)) and r.get("status") not in (None, "errored", "abandoned"):
            bucket = _find_bucket(buckets, r.get("task_class"), r.get("winner"), r.get("tool"))
            p90_ms = _bucket_p90(bucket)
            if p90_ms is not None and p90_ms > 0:
                actual_ms = duration_s * 1000
                if actual_ms > SCHEDULE_DIVERGENCE_FACTOR * p90_ms:
                    used = bucket.get("task_class") or bucket.get("tool") or "unknown"
                    node = bucket.get("node") or "any-node"
                    gaps.append(Gap("schedule_divergence", "info", pid,
                        f"actual {round(actual_ms)}ms vs bucket p90 {round(p90_ms)}ms "
                        f"(bucket {used}@{node}) — ran {round(actual_ms / p90_ms, 1)}x envelope"))
    return gaps


def scan_knowledge(capacity_path, stale_age_s: int = KNOWLEDGE_STALE_AGE_S) -> "list[Gap]":
    """Spell: knowledge_stale — knowledge/capacity.json is missing or older than
    the freshness SLO. Detection only, per the patrol/heal split: the 6h
    knowledge_rebuild gateway timer is the heal path, so this gap firing means
    that timer has been failing for >24h. IO lives here, not in scan_runs, so
    the run-record spells stay pure; import is local to avoid a
    toolsurface->health import at module load time (same as
    load_capacity_document).
    """
    from hearth.toolsurface._scope import resolve_in_scope

    import time

    gaps: "list[Gap]" = []
    try:
        resolved = resolve_in_scope(capacity_path)
    except (ValueError, OSError):
        return gaps
    if not resolved.is_file():
        gaps.append(Gap("knowledge_stale", "warn", "system",
                        "knowledge/capacity.json is missing"))
        return gaps
    age_s = time.time() - resolved.stat().st_mtime
    if age_s > stale_age_s:
        gaps.append(Gap("knowledge_stale", "warn", "system",
                        f"knowledge/capacity.json is stale ({int(age_s // 3600)}h old)"))
    return gaps


# Spell: rung state (ADR-0044) — a verdict from hearth.health.rungstate mapped
# onto the gap vocabulary. Only the four verdicts that mean "the rung answers but
# is not what the epoch promised" become gaps; `unreachable` is LIVENESS, which
# the watchdog's inventory probe (omen/llama-server :8082) owns, and
# `at_rate`/`no_baseline`/`unknown` carry no coherence claim to disagree with.
_RUNG_VERDICT_GAPS = {
    "degraded": ("rung_degraded", "high"),
    "stalled": ("rung_stalled", "high"),
    "warn": ("rung_warn", "warn"),
    "stale": ("rung_stale", "warn"),
}


def scan_rung_state(state) -> "list[Gap]":
    """Spells rung_degraded / rung_stalled (high) and rung_warn / rung_stale (warn).

    ``state`` is the dict ``hearth.health.rungstate.rung_state`` returns (pure —
    this function does no IO, so the caller decides how fresh the state is).
    The lab's failure modes this names (ADR-0043/0044): correct-but-degraded
    (pings fine, the deep probe decodes at 65 of a 106 tok/s epoch) and
    liveness-as-health (pings fine, no deep sample for 20 min — a rung nobody
    has measured is ``stale``, never ``at_rate``). ``plan_id`` is the rung name
    so the trend lookback keys it like any other gap. The detail repeats the
    epoch note: the envelope is of THIS baseline epoch, not of capacity, and no
    regime is named.
    """
    if not isinstance(state, dict):
        return []
    hit = _RUNG_VERDICT_GAPS.get(str(state.get("verdict", "")))
    if hit is None:
        return []
    kind, severity = hit
    rung = str(state.get("rung") or "omen-arc")
    detail = summarize_for_notes(state)
    note = state.get("note")
    if note:
        detail = f"{detail} — {note}"
    return [Gap(kind, severity, rung, detail)]


def summarize(gaps) -> dict:
    by_sev: dict = {}
    by_kind: dict = {}
    for g in gaps:
        by_sev[g.severity] = by_sev.get(g.severity, 0) + 1
        by_kind[g.kind] = by_kind.get(g.kind, 0) + 1
    return {"total": len(gaps), "by_severity": by_sev, "by_kind": by_kind}


def gaps_as_dicts(gaps) -> "list[dict]":
    return [asdict(g) for g in gaps]

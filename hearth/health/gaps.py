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

from dataclasses import dataclass, asdict

# No result.json this long after dispatch => phantom/stalled run holding occupancy.
PHANTOM_AGE_S = 1800  # 30 minutes

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


def scan_runs(records, phantom_age_s: int = PHANTOM_AGE_S) -> "list[Gap]":
    """Apply the coherence spells to a list of run records; return the gaps found.

    A record is a plain dict (shape produced by patrol._gather_runs):
    ``plan_id``, ``age_s``, ``has_result`` and — when ``has_result`` is true —
    ``status``, ``error``, ``stub``, ``winner``, ``winner_grade``,
    ``winner_files``, ``n_questions``, ``questions_text``, ``promoted``.
    """
    gaps: "list[Gap]" = []
    for r in records:
        pid = r.get("plan_id", "?")
        age = r.get("age_s", 0) or 0

        # Spell: phantom_in_flight — claims running, produced nothing for too long.
        if not r.get("has_result"):
            if age >= phantom_age_s:
                gaps.append(Gap("phantom_in_flight", "warn", pid,
                    f"no result after {age // 60} min — reads in-flight but is stalled/errored; "
                    f"holds phantom occupancy"))
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
    return gaps


def summarize(gaps) -> dict:
    by_sev: dict = {}
    by_kind: dict = {}
    for g in gaps:
        by_sev[g.severity] = by_sev.get(g.severity, 0) + 1
        by_kind[g.kind] = by_kind.get(g.kind, 0) + 1
    return {"total": len(gaps), "by_severity": by_sev, "by_kind": by_kind}


def gaps_as_dicts(gaps) -> "list[dict]":
    return [asdict(g) for g in gaps]

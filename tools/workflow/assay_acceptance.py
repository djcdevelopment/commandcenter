"""Stream-scoped acceptance gate: verify required deliverables BEFORE ranking laps.

A stream declares its required deliverables via a CCMETA `requires` field in the
work item header.  Absent or empty `requires` is a no-op — every lap passes
acceptance and ranking is byte-identical to today.  When `requires` is present,
every lap branch is checked via `git ls-tree -r --name-only`; any lap missing a
required glob is marked `acceptance_failed` and excluded from the behavior ranking
entirely (not down-weighted) before ranking runs.

If ALL laps fail acceptance, the result carries outcome="no_winner" so the caller
surfaces a needs-curation signal rather than silently crowning the least-bad lap.

Note: agent_timed_out / agent_rc stay in the scoreboard as observation metadata;
they must NOT gate the winner.  Only deliverable presence gates.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess


# ---------------------------------------------------------------------------
# CCMETA parsing
# ---------------------------------------------------------------------------

_CCMETA_RE = re.compile(r"<!--\s*CCMETA\s*(.*?)\s*-->", re.DOTALL)


def parse_ccmeta_requires(work_item_text: str) -> list[str]:
    """Return the `requires` glob list from a CCMETA header, or [] if absent/empty."""
    match = _CCMETA_RE.search(work_item_text)
    if not match:
        return []
    try:
        meta = json.loads(match.group(1))
        return list(meta.get("requires") or [])
    except (json.JSONDecodeError, AttributeError):
        return []


# ---------------------------------------------------------------------------
# Branch file enumeration
# ---------------------------------------------------------------------------

def list_branch_files(branch: str) -> list[str]:
    """Return all file paths in a git branch via `git ls-tree -r --name-only`."""
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", branch],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


# ---------------------------------------------------------------------------
# Per-lap acceptance check
# ---------------------------------------------------------------------------

def check_lap_acceptance(
    branch: str,
    requires: list[str],
    list_files_fn=None,
) -> dict:
    """Check whether a single lap branch satisfies every required-deliverable glob.

    Returns:
        passed (bool): True iff every glob matched at least one file.
        missing_globs (list[str]): Globs that had no matching path.
    """
    if not requires:
        return {"passed": True, "missing_globs": []}
    files = (list_files_fn or list_branch_files)(branch)
    missing = [
        glob for glob in requires
        if not any(fnmatch.fnmatch(f, glob) for f in files)
    ]
    return {"passed": not missing, "missing_globs": missing}


# ---------------------------------------------------------------------------
# Scoreboard filter
# ---------------------------------------------------------------------------

def filter_scoreboard_by_acceptance(
    scoreboard: list[dict],
    requires: list[str],
    list_files_fn=None,
) -> tuple[list[dict], list[dict]]:
    """Split scoreboard into (accepted, rejected) based on deliverable presence.

    When `requires` is empty this is a strict no-op: the returned accepted list
    is the same objects as the input scoreboard, and rejected is [].  Ranking on
    the accepted list is therefore byte-identical to ranking on the full scoreboard.
    """
    if not requires:
        return list(scoreboard), []

    accepted: list[dict] = []
    rejected: list[dict] = []
    for entry in scoreboard:
        result = check_lap_acceptance(entry["branch"], requires, list_files_fn)
        if result["passed"]:
            accepted.append(entry)
        else:
            rejected.append(
                {**entry, "acceptance_failed": True, "missing_globs": result["missing_globs"]}
            )
    return accepted, rejected


# ---------------------------------------------------------------------------
# Ranked result
# ---------------------------------------------------------------------------

def rank_with_acceptance(
    scoreboard: list[dict],
    requires: list[str],
    list_files_fn=None,
) -> dict:
    """Apply stream-scoped acceptance then rank surviving laps by behavior_score.

    Returns a dict with:
        outcome:  "winner_selected" | "no_winner"
        winner:   str | None  — worker id of the winning lap
        accepted: list[dict]  — laps that passed acceptance (unmodified scoreboard entries)
        rejected: list[dict]  — laps that failed (with acceptance_failed=True, missing_globs)
        reason:   str         — human-readable explanation
    """
    accepted, rejected = filter_scoreboard_by_acceptance(scoreboard, requires, list_files_fn)

    if not accepted:
        return {
            "outcome": "no_winner",
            "winner": None,
            "reason": "all laps failed stream acceptance — needs curation",
            "accepted": accepted,
            "rejected": rejected,
        }

    ranked = sorted(
        accepted,
        key=lambda e: e.get("behavior_score", e.get("score", 0)),
        reverse=True,
    )
    winner_entry = ranked[0]
    return {
        "outcome": "winner_selected",
        "winner": winner_entry["worker"],
        "reason": (
            f"acceptance passed: {len(accepted)}/{len(scoreboard)} laps qualified; "
            "winner by behavior_score"
        ),
        "accepted": accepted,
        "rejected": rejected,
        "ranked": ranked,
    }

"""Flat single-shot backend benchmark: gcp-gemini vs gcp-gemini-pro vs am4-moe
on doc-vs-ADR-vs-code consistency tasks (Track 1 of the GCP trial-credit
benchmark; see the implementation plan for the full two-track design).

Unlike ``matrix.py``'s planner<->critic ``run_refine`` loop, each task here is
ONE ``local_generate`` call per backend, scored by the same held-out judge
panel (``matrix.score_proposal``). Reuses ``matrix.py``'s ``PROMPTS``-dict
convention and scoring/aggregation shape, but flat instead of a refine loop --
the right shape for "does backend A answer this documentation-consistency
task better, cheaper, or faster than backend B," not "does refining help."

Every call pins ``backend=`` explicitly and never passes ``task=``, so
``_resolve_target`` (``hearth/toolsurface/inference.py``) always reports
``routed_by="pinned:<name>"`` -- a controlled comparison requires every
dispatch to land on the named backend, never tag-routed.
"""

from __future__ import annotations

from typing import Callable, Optional

from hearth.experiments.matrix import DEFAULT_JUDGES, score_proposal
from hearth.projection.gemini_pricing import cost_usd

# ---- Doc-vs-ADR-vs-code consistency tasks, built from real repo files ----
# Each task packs real ADR/code/README files (well under local_generate's
# files= caps: 256 KiB/file, 1 MiB total per call) so the comparison is on
# genuine documentation-consistency work, not a synthetic prompt.
DOC_ADR_TASKS: dict[str, dict] = {
    "adr-vs-code-fail-closed": {
        "prompt": (
            "ADR-0023 claims HEARTH's capability-profile system is fail-closed: a "
            "caller with no profile, or a profile granting nothing, is denied every "
            "tool. Read the ADR and the two code files below. Does the code actually "
            "implement what the ADR claims? Cite the exact function/behavior that "
            "proves or disproves it, and name any gap."
        ),
        "files": [
            "docs/adr/0023-authority-is-granted-never-assumed.md",
            "hearth/kernel/capabilities.py",
            "hearth/etc/profiles.toml",
        ],
    },
    "adr-vs-code-container-access": {
        "prompt": (
            "ADR-0022 amends ADR-0019, claiming the real blocker to container access "
            "was the MCP SDK's DNS-rebinding allowlist rather than the bind address, "
            "and that ADR-0019's authorization model (sections 2-5) is unchanged. "
            "Read both ADRs and the gateway code below. Reconcile the two ADRs against "
            "current code: is 0022's supersession claim accurate, and is 0019's "
            "authorization model really untouched?"
        ),
        "files": [
            "docs/adr/0022-container-access-needs-no-exposure.md",
            "docs/adr/0019-container-access-capability-profiles.md",
            "hearth/kernel/gateway.py",
        ],
    },
    "cross-repo-adr-drift": {
        "prompt": (
            "registry/constellation.toml and ADR-0017 both describe a planned "
            "'consumer slice' loader for the constellation registry as NOT YET BUILT. "
            "Read both files. Is that claim still true, or does anything in the "
            "registry file itself suggest the loader has since landed? State your "
            "confidence and what you would need to check to be certain."
        ),
        "files": [
            "registry/constellation.toml",
            "docs/adr/0017-software-constellation-registry-am4-seed-intake.md",
        ],
    },
    "heterogeneous-adr-dirs": {
        "prompt": (
            "This repo merges two projects and may have more than one ADR directory. "
            "Read the README and the two sample ADRs below (each from a different "
            "part of the repo). Identify whether they belong to a single unified ADR "
            "convention or two separate ones, and name the concrete differences "
            "(numbering, status vocabulary, structure) between them."
        ),
        "files": [
            r"C:\work\baseline\README.md",
            r"C:\work\baseline\fieldlab\docs\adr\0008-liveness-is-not-admission.md",
            r"C:\work\baseline\Lumberjacks\docs\adrs\0002-edge-nodes-assist-but-do-not-own-truth.md",
        ],
    },
    "plan-bounded-remediation": {
        "prompt": (
            "Read the ADR and code below. Produce a BOUNDED remediation/verification "
            "plan (ordered steps, one per concern) for any gap between what the ADR "
            "claims and what the code does -- or state explicitly that none exists. "
            "Do not propose a rewrite; propose the smallest verifiable change."
        ),
        "files": [
            "docs/adr/0023-authority-is-granted-never-assumed.md",
            "hearth/kernel/capabilities.py",
        ],
    },
}


def run_flat_cell(task_id: str, backend: str, generate: Callable[..., dict],
                  judges: Optional[list[tuple]] = None) -> dict:
    """Run one benchmark task against one PINNED backend; score with the judge panel.

    Passes ``backend=`` alone (never ``task=``) so the dispatch is always a
    deliberate pin, never tag-routed -- required for a controlled comparison.
    """
    judges = judges if judges is not None else DEFAULT_JUDGES
    task = DOC_ADR_TASKS[task_id]
    result = generate(prompt=task["prompt"], files=task["files"], backend=backend)
    score = (score_proposal(result.get("text") or "", task["prompt"], judges, generate)
             if result.get("ok") else None)
    return {
        "task_id": task_id,
        "backend": backend,
        "model": result.get("model"),
        "ok": result.get("ok"),
        "routed_by": result.get("routed_by"),
        "tokens_in": result.get("tokens_in"),
        "tokens_out": result.get("tokens_out"),
        "duration_ms": result.get("duration_ms"),
        "cost_usd": cost_usd(backend, result.get("model"),
                             result.get("tokens_in"), result.get("tokens_out")),
        "score": score,
        "error": result.get("error"),
    }


def run_flat_matrix(backends: list[str], generate: Callable[..., dict],
                    task_ids: Optional[list[str]] = None,
                    judges: Optional[list[tuple]] = None,
                    on_progress: Optional[Callable[[str], None]] = None) -> list[dict]:
    """Sweep every (backend, task) pair -> dataset rows."""
    task_ids = task_ids or list(DOC_ADR_TASKS.keys())
    rows: list[dict] = []
    for backend in backends:
        for task_id in task_ids:
            if on_progress:
                on_progress(f"{backend}: {task_id}")
            rows.append(run_flat_cell(task_id, backend, generate, judges=judges))
    return rows


def bench_summary(rows: list[dict]) -> dict:
    """Aggregate: mean score / mean+total cost / mean latency, each by backend."""
    def _mean(vals):
        v = [x for x in vals if x is not None]
        return round(sum(v) / len(v), 4) if v else None

    def _total(vals):
        v = [x for x in vals if x is not None]
        return round(sum(v), 6) if v else None

    by_score: dict[str, list] = {}
    by_cost: dict[str, list] = {}
    by_latency: dict[str, list] = {}
    for r in rows:
        b = r["backend"]
        by_score.setdefault(b, []).append((r.get("score") or {}).get("mean"))
        by_cost.setdefault(b, []).append(r.get("cost_usd"))
        by_latency.setdefault(b, []).append(r.get("duration_ms"))

    return {
        "cells": len(rows),
        "ok_cells": sum(1 for r in rows if r.get("ok")),
        "mean_score_by_backend": {k: _mean(v) for k, v in sorted(by_score.items())},
        "mean_cost_usd_by_backend": {k: _mean(v) for k, v in sorted(by_cost.items())},
        "total_cost_usd_by_backend": {k: _total(v) for k, v in sorted(by_cost.items())},
        "mean_latency_ms_by_backend": {k: _mean(v) for k, v in sorted(by_latency.items())},
    }

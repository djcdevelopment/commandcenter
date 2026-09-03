"""Flat single-shot arm benchmark on doc-vs-ADR-vs-code consistency tasks
(Track 1 of the GCP trial-credit benchmark; see the implementation plan for
the full two-track design).

Unlike ``matrix.py``'s planner<->critic ``run_refine`` loop, each task here is
ONE ``local_generate`` call per arm, scored by a held-out judge panel
(``matrix.score_proposal``). Reuses ``matrix.py``'s ``PROMPTS``-dict
convention and scoring/aggregation shape, but flat instead of a refine loop --
the right shape for "does arm A answer this documentation-consistency task
better, cheaper, or faster than arm B," not "does refining help."

Every call pins ``backend=`` explicitly and never passes ``task=``, so
``_resolve_target`` (``hearth/toolsurface/inference.py``) always reports
``routed_by="pinned:<name>"`` -- a controlled comparison requires every
dispatch to land on the named backend, never tag-routed.

Arms (M1a, 2026-09-03)
----------------------
An arm is a rung, or a seat on a rung: ``"omen-arc"`` (the rung's default
model), ``("omen-swap", "phi4-vk1")``, or the CLI spelling
``"omen-swap:phi4-vk1"`` (split on the FIRST colon, so ``fx99-ollama:qwen2.5:7b``
keeps its Ollama tag). ``model=`` is threaded to the generate callable; the
door's ``_apply_defaults`` keeps a caller's model, so two seats on one rung
are two distinct ``model_id``s in the artifacts -- the ADR-0027 gate-2 shape.
Rows keep ``backend`` and ``model`` (as served) plus ``model_requested`` and
the ``arm`` label; ``bench_summary`` aggregates by arm so two models on one
backend never collapse into one column.

The sweep runs under ``dispatch_identity(DispatchIdentity("experiment-doc-adr-bench",
"local", <node>))`` (``hearth.observation.identity``). Absent identity means
no observation -- until M1a no experiment pushed one, so none of their rows
counted as evidence. The judge panel is held out from every arm
(``hearth.experiments.panel``): a judge sharing a backend or a model with an
arm is refused before any token is spent.

The two pours to run once OMEN is free (the imagegen lane holds the B70 pool
at the time of writing; nothing here dispatches until then)
----------------------------------------------------------------------------
Payload arithmetic first: ``omen-swap`` declares ``context_bytes = 14336``
(ADR-0031 MIN over its members) and a pinned call whose packed payload exceeds
that is REFUSED at the door (``routing_refusal`` /
``payload_over_budget_for_pinned_backend``). Every original task packs more
than that (the smallest, ``cross-repo-adr-drift``, is ~14.4 KB), so the pours
use the two compact tasks added for them -- ``adr-0042-vs-launcher`` (~8.9 KB)
and ``adr-0041-claims-vs-receipts`` (~9.3 KB). ``--dry-run`` prints the
per-cell estimate against the rung's budget as ``fits``.

(a) gate-2 unlock for the OMEN rung -- two side models on ``omen-swap``,
    inside P11's rotation window after ``rotation_load`` has asserted both
    placements (ADR-0042) and ``/running`` lists production plus both::

        python -m hearth.experiments.run_doc_adr_bench --dry-run \\
            --arms omen-swap:phi4-vk1 omen-swap:qwen14b-vk1 \\
            --tasks adr-0042-vs-launcher adr-0041-claims-vs-receipts
        python -m hearth.experiments.run_doc_adr_bench --smoke \\
            --arms omen-swap:phi4-vk1 --tasks adr-0042-vs-launcher     # one call; read the artifact
        python -m hearth.experiments.run_doc_adr_bench \\
            --arms omen-swap:phi4-vk1 omen-swap:qwen14b-vk1 \\
            --tasks adr-0042-vs-launcher adr-0041-claims-vs-receipts \\
            --max-tokens 1024 --timeout-s 600
        python -m hearth.projection.rebuild

    Panel = ``held_out_judges(arms)``: gcp-gemini, fx99-ollama, omen-arc
    (co-resident on the same B70s -- ``panel_note``; scores are unaffected but
    the arm's timing rows are captured before the judges run), gcp-gemini-pro.
    Acceptance: artifacts show two distinct ``model_id``s on ``backend=omen-swap``;
    ``knowledge/capabilities.json`` ``capability_count`` 1 -> 2 with
    ``capability:task_kind=offload-generate|backend=omen-swap``; the watermark
    moves off 2026-07-31; ``exclusions.ndjson`` explains every excluded call.
    Two door calls from ``claude-frontier`` (a second workflow) close gate 1.

(b) fallback if the side port slips -- two models on the fx99 sidecar
    (``context_bytes = 24576``; the compact tasks and ``cross-repo-adr-drift``
    fit), still a local-rung gate-2 unlock::

        python -m hearth.experiments.run_doc_adr_bench \\
            --arms fx99-ollama:qwen2.5:7b fx99-ollama:qwen2.5:14b \\
            --tasks adr-0042-vs-launcher adr-0041-claims-vs-receipts

    The pool's ``fx99-ollama/qwen2.5:14b`` judge seat is dropped (it IS an
    arm); the panel is gcp-gemini, omen-arc, gcp-gemini-pro.

Smoke one call before either batch: corpus_guard monotonicity makes a bad pour
expensive to unwind.
"""

from __future__ import annotations

import os
import platform
from dataclasses import asdict
from typing import Any, Callable, Iterable, Optional

from hearth.experiments.matrix import DEFAULT_JUDGES, score_proposal
from hearth.experiments.panel import Panel, assert_held_out, coerce_seat, held_out_judges
from hearth.observation.identity import DispatchIdentity, dispatch_identity
from hearth.projection.gemini_pricing import cost_usd

Arm = tuple[str, Optional[str]]   # (backend, model); model None = the rung's default

BENCH_CALLER_ID = "experiment-doc-adr-bench"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    # Added 2026-08-29 for the mechnet-forward program: the Phase 0 smoke should
    # produce Phase 1's actual deliverable instead of a throwaway. The three arms
    # each draft the ADR-0043 cutover record from the two records that constrain
    # it plus the incumbent's real launch script, so the spread is also the first
    # honest read on whether a local rung can carry doc work.
    "adr-0043-cutover-record-draft": {
        "prompt": (
            "OMEN currently serves one resident model via the launch script below, "
            "started by a scheduled task. We are about to put llama-swap in front of "
            "it so more than one model can be served by name, with the incumbent "
            "staying resident and specialists loading beside it. Read the two ADRs "
            "and the launch script below, then draft the cutover decision record. "
            "It MUST state: what changes and what deliberately does not; how the two "
            "existing ADRs constrain the design (be specific about what each one "
            "forbids); which checks must pass before the change is trusted, each "
            "phrased so an operator can actually observe it pass or fail; and the "
            "exact rollback. Do not invent measurements or cite numbers not present "
            "in the sources. Where a fact is needed but absent, write TODO and say "
            "what must be measured."
        ),
        "files": [
            "docs/adr/0041-co-residency-poisons-the-incumbent.md",
            "docs/adr/0042-devices-are-selected-by-type-never-by-index.md",
            "fleet/arcserve/serve-arc.cmd",
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
    # Added 2026-09-03 (M1a) for the two-model pours on the side seats: every
    # task above packs more than omen-swap's declared context_bytes (14336,
    # ADR-0031 MIN over its members) and would be refused at the door. These
    # two are the same kind of work -- an ADR held against the code it
    # governs -- sized to fit a -c 4096 seat with margin.
    "adr-0042-vs-launcher": {
        "prompt": (
            "ADR-0042 rules that GPU devices are selected by TYPE and asserted from "
            "the server's own load report, never trusted from an index. Read the ADR "
            "and the production launch script below. Does the script obey the rule? "
            "Cite the exact flag or line that selects devices, say whether anything "
            "in the script still relies on a device index, and name what an operator "
            "would have to read to confirm the placement after a launch."
        ),
        "files": [
            "docs/adr/0042-devices-are-selected-by-type-never-by-index.md",
            "fleet/arcserve/serve-arc.cmd",
        ],
    },
    "adr-0041-claims-vs-receipts": {
        "prompt": (
            "Read the ADR below. List every quantitative claim it makes (a number, a "
            "percentage, a rate, a duration) and, for each one, whether the ADR names "
            "the receipt or measurement it came from. Flag any figure that has no "
            "named source. Do not add figures of your own."
        ),
        "files": [
            "docs/adr/0041-co-residency-poisons-the-incumbent.md",
        ],
    },
}


# ---- Arms ------------------------------------------------------------------
def parse_arm(spec: Any) -> Arm:
    """Normalise an arm spec to ``(backend, model)``.

    Accepts ``"backend"`` (the rung's default model), ``"backend:model"``
    (split on the first colon only -- ``fx99-ollama:qwen2.5:7b`` is the
    ``qwen2.5:7b`` seat on ``fx99-ollama``), a ``(backend, model)`` pair or
    list, or a ``{"backend", "model"}`` dict. The backend is required; an
    unpinned (``None``) backend is not an arm -- a controlled comparison names
    every rung it lands on.
    """
    if isinstance(spec, str):
        text = spec.strip()
        if not text:
            raise ValueError("arm must name a backend")
        backend, sep, model = text.partition(":")
        backend, model = backend.strip(), model.strip()
        if not backend or (sep and not model):
            raise ValueError(f"arm must be 'backend' or 'backend:model', got {spec!r}")
        return (backend, model or None)
    if isinstance(spec, dict):
        backend, model = spec.get("backend"), spec.get("model")
    elif isinstance(spec, (tuple, list)):
        if len(spec) != 2:
            raise ValueError(f"arm must be (backend, model), got {spec!r}")
        backend, model = spec
    else:
        raise ValueError(f"cannot read an arm from {spec!r}")
    if not isinstance(backend, str) or not backend.strip():
        raise ValueError(f"arm must name a backend, got {spec!r}")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError(f"arm model must be a non-empty string or None, got {spec!r}")
    return (backend.strip(), model.strip() if model else None)


def arm_label(arm: Any) -> str:
    """``backend`` or ``backend:model`` -- the row/summary key for an arm."""
    backend, model = parse_arm(arm)
    return f"{backend}:{model}" if model else backend


def bench_identity(node: Optional[str] = None) -> DispatchIdentity:
    """The identity every sweep dispatches under, so its rows count as evidence."""
    return DispatchIdentity(BENCH_CALLER_ID, "local", node or platform.node())


# ---- Cells -----------------------------------------------------------------
def run_flat_cell(task_id: str, arm: Any, generate: Callable[..., dict],
                  judges: Optional[Iterable[tuple]] = None, *,
                  max_tokens: Optional[int] = None, timeout_s: Optional[int] = None,
                  pool: Any = None) -> dict:
    """Run one benchmark task against one PINNED arm; score with the judge panel.

    Passes ``backend=`` and ``model=`` (never ``task=``) so the dispatch is
    always a deliberate pin, never tag-routed -- required for a controlled
    comparison. ``max_tokens`` / ``timeout_s`` are forwarded only when given,
    so an omitted value leaves the rung's declared defaults in force (the
    thinking rungs need their generous output budget). Judges keep
    ``score_proposal``'s own budget.

    Refuses (``PanelConflict``) before any call when a judge shares a backend
    or a model with the arm; ``judges=None`` falls back to ``DEFAULT_JUDGES``
    under the same check. ``pool`` injects the backend declaration used to
    resolve seats (None = the declared pool file).
    """
    backend, model = parse_arm(arm)
    judges = judges if judges is not None else DEFAULT_JUDGES
    assert_held_out(judges, [(backend, model)], pool=pool)
    task = DOC_ADR_TASKS[task_id]
    kwargs: dict[str, Any] = {"prompt": task["prompt"], "files": task["files"],
                              "backend": backend, "model": model}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if timeout_s is not None:
        kwargs["timeout_s"] = timeout_s
    result = generate(**kwargs)
    served_model = result.get("model") or model
    score = (score_proposal(result.get("text") or "", task["prompt"], judges, generate)
             if result.get("ok") else None)
    return {
        "task_id": task_id,
        "arm": arm_label((backend, model)),
        "backend": backend,
        "model": served_model,
        "model_requested": model,
        "ok": result.get("ok"),
        "routed_by": result.get("routed_by"),
        # Persist what was actually generated. Without this the bench scores an
        # answer and then throws it away, so a row can say 91/100 and leave you
        # unable to read the thing that earned it, re-judge it under another
        # rubric, or use it -- the 2026-08-29 three-arm run produced a usable ADR
        # draft that had to be regenerated to be read. A benchmark that keeps only
        # the grade is not evidence, it is a rumour about evidence.
        "text": result.get("text"),
        "tokens_in": result.get("tokens_in"),
        "tokens_out": result.get("tokens_out"),
        "duration_ms": result.get("duration_ms"),
        "cost_usd": cost_usd(backend, served_model,
                             result.get("tokens_in"), result.get("tokens_out")),
        "score": score,
        "error": result.get("error"),
        "error_code": result.get("error_code"),
    }


def _arm_seats(arms: Iterable[Any]) -> list[Arm]:
    seats = [parse_arm(a) for a in arms]
    if not seats:
        raise ValueError("arms must name at least one backend[:model] under test")
    return seats


def _task_ids(task_ids: Optional[Iterable[str]]) -> list[str]:
    ids = list(task_ids) if task_ids else list(DOC_ADR_TASKS)
    unknown = [t for t in ids if t not in DOC_ADR_TASKS]
    if unknown:
        raise KeyError(f"unknown task id(s) {unknown}; have {list(DOC_ADR_TASKS)}")
    return ids


def run_flat_matrix(arms: Iterable[Any], generate: Callable[..., dict],
                    task_ids: Optional[Iterable[str]] = None,
                    judges: Optional[Iterable[tuple]] = None,
                    on_progress: Optional[Callable[[str], None]] = None, *,
                    max_tokens: Optional[int] = None, timeout_s: Optional[int] = None,
                    pool: Any = None,
                    identity: Optional[DispatchIdentity] = None) -> list[dict]:
    """Sweep every (arm, task) pair -> dataset rows, under the bench identity.

    ``judges=None`` derives the panel with ``held_out_judges(arms)``; an
    explicit panel is checked with ``assert_held_out`` -- either way the whole
    sweep is refused before the first call. Every dispatch (arms and judges)
    runs inside ``dispatch_identity(identity or bench_identity())`` so the
    observation emitter records it as evidence.
    """
    seats = _arm_seats(arms)
    ids = _task_ids(task_ids)
    if judges is None:
        judges = held_out_judges(seats, pool=pool)
    assert_held_out(judges, seats, pool=pool)
    rows: list[dict] = []
    with dispatch_identity(identity or bench_identity()):
        for seat in seats:
            for task_id in ids:
                if on_progress:
                    on_progress(f"{arm_label(seat)}: {task_id}")
                rows.append(run_flat_cell(task_id, seat, generate, judges=judges,
                                          max_tokens=max_tokens, timeout_s=timeout_s,
                                          pool=pool))
    return rows


# ---- Dry-run plan ----------------------------------------------------------
def _resolve_task_file(path: str, repo_root: str) -> str:
    return path if os.path.isabs(path) else os.path.join(repo_root, path)


def task_payload_bytes(task: dict, repo_root: Optional[str] = None) -> dict:
    """Pre-flight estimate of what the door will pack for one task.

    Mirrors ``_pack_files``' arithmetic (prompt + file bytes + the
    ``<file path=...>`` wrappers) without reading a file: sizes come from
    ``os.stat``. A missing file is reported, not raised -- the door raises at
    dispatch; a dry run should just say so. The estimate is the number the
    ADR-0031 pin check compares against a rung's ``context_bytes``.
    """
    root = repo_root or _REPO_ROOT
    total = len(task["prompt"].encode("utf-8"))
    missing: list[str] = []
    for path in task.get("files") or []:
        resolved = _resolve_task_file(path, root)
        if not os.path.isfile(resolved):
            missing.append(path)
            continue
        total += os.stat(resolved).st_size + len(path) + 27   # <file path="..">\n..\n</file>\n\n
    return {"payload_bytes_est": total, "files_missing": missing}


def _context_budgets(pool: Any) -> dict[str, Optional[int]]:
    """backend -> declared context_bytes (None = unlimited); {} when unreadable."""
    try:
        if pool is None:
            from hearth.toolsurface.backends import load_pool
            pool = load_pool()
        return {b.name: b.context_bytes() for b in pool.backends}
    except Exception:
        return {}


def plan_flat_matrix(arms: Iterable[Any], task_ids: Optional[Iterable[str]] = None,
                     judges: Optional[Iterable[tuple]] = None, *, pool: Any = None,
                     identity: Optional[DispatchIdentity] = None,
                     repo_root: Optional[str] = None) -> dict:
    """What a sweep WOULD do: cells, panel, identity, payload-vs-budget -- no dispatch.

    Raises ``PanelConflict`` exactly where ``run_flat_matrix`` would, so a dry
    run proves the panel before a token is spent. Each cell carries
    ``payload_bytes_est`` against the arm rung's ``context_bytes`` as ``fits``
    (None when the rung is unlimited or the pool is unreadable) -- the ADR-0031
    pin refusal, computed before the door has to.
    """
    seats = _arm_seats(arms)
    ids = _task_ids(task_ids)
    panel: Optional[Panel] = None
    if judges is None:
        panel = held_out_judges(seats, pool=pool)
        judges = panel
    else:
        assert_held_out(judges, seats, pool=pool)
    judge_seats = [coerce_seat(j) for j in judges]
    budgets = _context_budgets(pool)
    cells: list[dict] = []
    for backend, model in seats:
        budget = budgets.get(backend)
        for task_id in ids:
            est = task_payload_bytes(DOC_ADR_TASKS[task_id], repo_root)
            fits = None if budget is None else est["payload_bytes_est"] <= budget
            cells.append({"task_id": task_id, "arm": arm_label((backend, model)),
                          "backend": backend, "model": model, "context_bytes": budget,
                          **est, "fits": fits})
    ident = identity or bench_identity()
    return {
        "identity": asdict(ident),
        "arms": [arm_label(s) for s in seats],
        "tasks": ids,
        "judges": [list(j) for j in judge_seats],
        "panel": panel.as_dict() if panel is not None else None,
        "panel_note": panel.panel_note if panel is not None else None,
        "cells": cells,
        "planned_calls": len(cells) * (1 + len(judge_seats)),
    }


# ---- Aggregation -----------------------------------------------------------
def bench_summary(rows: list[dict]) -> dict:
    """Aggregate: mean score / mean+total cost / mean latency, each by ARM.

    The key is the row's ``arm`` label (``backend``, or ``backend:model`` for a
    pinned seat) -- two models on one backend are two columns. Rows written
    before M1a carry no ``arm`` and fall back to ``backend``, so old datasets
    summarise exactly as they did.
    """
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
        b = r.get("arm") or r["backend"]
        by_score.setdefault(b, []).append((r.get("score") or {}).get("mean"))
        by_cost.setdefault(b, []).append(r.get("cost_usd"))
        by_latency.setdefault(b, []).append(r.get("duration_ms"))

    return {
        "cells": len(rows),
        "ok_cells": sum(1 for r in rows if r.get("ok")),
        "arms": sorted(by_score),
        "mean_score_by_backend": {k: _mean(v) for k, v in sorted(by_score.items())},
        "mean_cost_usd_by_backend": {k: _mean(v) for k, v in sorted(by_cost.items())},
        "total_cost_usd_by_backend": {k: _total(v) for k, v in sorted(by_cost.items())},
        "mean_latency_ms_by_backend": {k: _mean(v) for k, v in sorted(by_latency.items())},
    }

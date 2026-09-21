"""Offline learning over accumulated operator runs (Gate 6).

`operator learn report` reads every run under runs/operator/ -- the replayed
RUN-STATE.json, the frozen envelope, the proposals, and the attempt receipts --
groups the outcomes by the requirement's dimensions (route target, host, model,
task type, risk), and writes two projections:

  * knowledge/operator_learning.json  -- byte-stable (no wall clock; the inputs
                                         are named by run id and digest)
  * OPERATOR-LEARNING.html            -- the same report for a reader

It only RECOMMENDS. Nothing here edits loops.toml, backends.toml, policy, or a
catalog status. A recommendation becomes real through a recorded decision, an
ADR, and an acceptance gate recorded as a run (plan rev 3.1, "Learning, offline
only"). Runs flagged test_mode are excluded unless asked for (D-113).
"""
from __future__ import annotations

import html
import json
import math
from pathlib import Path
from typing import Any, Optional

from . import paths
from .workload import workload_key

CONTRACT_VERSION = "operator-learning.v1"


def _read_json(target: Path) -> Optional[Any]:
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _collect_runs(include_test_mode: bool = False) -> list[dict]:
    """One row per run that has a replayed state and at least one attempt."""
    rows: list[dict] = []
    runs_dir = paths.runs_dir()
    if not runs_dir.is_dir():
        return rows
    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir() or run_dir.name.startswith("_"):
            continue
        state = _read_json(run_dir / "RUN-STATE.json")
        if not isinstance(state, dict):
            continue
        if state.get("test_mode") and not include_test_mode:
            continue
        refs = run_dir / "refs"
        envelope = _read_json(refs / "envelope.json") or {}
        attempts = [a for a in (_read_json(p) for p in sorted(refs.glob("attempt_*.json")))
                    if isinstance(a, dict)]
        if not attempts:
            continue
        # The attempt that produced the outcome is the last one; earlier attempts
        # (if any) are counted, never averaged away.
        receipt = attempts[-1]
        proposal = _read_json(refs / f"proposal_{receipt.get('proposal_id')}.json") or {}
        classification = envelope.get("classification") or {}
        usage = receipt.get("usage") or {}
        try:
            duration = float(receipt.get("duration_s"))
        except (TypeError, ValueError):
            duration = None
        rows.append({
            "run_id": run_dir.name,
            "status": state.get("status"),
            "reconstructable": bool(state.get("reconstructable")),
            "history_verified": bool(state.get("history_verified")),
            "test_mode": bool(state.get("test_mode")),
            "attempts": len(attempts),
            "target": receipt.get("target"),
            "route_kind": receipt.get("route_kind"),
            "host": receipt.get("host"),
            "endpoint": receipt.get("endpoint"),
            "model": receipt.get("model"),
            "attempt_status": receipt.get("status"),
            "duration_s": duration,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "tool_calls": usage.get("tool_calls"),
            "task_type": classification.get("task_type"),
            "risk_level": classification.get("risk_level"),
            "mutation_level": classification.get("mutation_level"),
            "max_context_tokens": (envelope.get("constraints") or {}).get("max_context_tokens"),
            "workload_key": workload_key(envelope),
            "input_paths": len((envelope.get("inputs") or {}).get("paths") or []),
            "rejected_proposals": max(0, len(state.get("proposals") or []) - 1),
            "catalog_version": receipt.get("catalog_version"),
            "attempt_id": receipt.get("attempt_id"),
            "artifact_sha256": receipt.get("artifact_sha256"),
            "proposal_rationale": proposal.get("rationale"),
        })
    return rows


def _group(rows: list[dict], key: str) -> dict[str, dict]:
    groups: dict[str, dict] = {}
    for row in rows:
        name = str(row.get(key))
        g = groups.setdefault(name, {"runs": 0, "completed": 0, "reconstructable": 0,
                                     "durations_s": [], "prompt_tokens": 0,
                                     "completion_tokens": 0, "run_ids": []})
        g["runs"] += 1
        g["completed"] += 1 if row.get("status") == "completed" else 0
        g["reconstructable"] += 1 if row.get("reconstructable") else 0
        if row.get("duration_s") is not None:
            g["durations_s"].append(row["duration_s"])
        g["prompt_tokens"] += int(row.get("prompt_tokens") or 0)
        g["completion_tokens"] += int(row.get("completion_tokens") or 0)
        g["run_ids"].append(row["run_id"])
    for g in groups.values():
        d = g.pop("durations_s")
        g["duration_s"] = {"min": min(d), "max": max(d), "mean": round(sum(d) / len(d), 3),
                           "n": len(d)} if d else None
    return dict(sorted(groups.items()))


def _comparable_workloads(rows: list[dict]) -> dict[str, list[dict]]:
    """Group recorded work, never infer comparability from a file count alone."""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        duration, count = row.get("duration_s"), row.get("input_paths")
        key, target = row.get("workload_key"), row.get("target")
        if (row.get("status") != "completed" or row.get("history_verified") is not True
                or row.get("reconstructable") is not True or row.get("test_mode") is not False
                or row.get("task_type") != "engineering"
                or type(count) is not int or not 0 <= count <= 3
                or type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0
                or not isinstance(key, str) or len(key) != 64
                or any(c not in "0123456789abcdef" for c in key)
                or not isinstance(target, str) or not target):
            continue
        groups.setdefault(key, []).append(row)
    return dict(sorted(groups.items()))


def _recommendations(rows: list[dict], by_target: dict[str, dict]) -> list[dict]:
    """Recommendations are statements with the evidence that produced them.

    Every recommendation carries `applied: false` and the path a decision would
    take (D-xxx + ADR + acceptance gate). Nothing here applies anything.
    """
    recs: list[dict] = []
    # 1. Compare routes only inside an identical recorded workload group.
    for key, small in _comparable_workloads(rows).items():
        by_t: dict[str, list[dict]] = {}
        for r in small:
            by_t.setdefault(str(r["target"]), []).append(r)
        if len(by_t) < 2:
            continue
        ranked = sorted(((sum(x["duration_s"] for x in v) / len(v), t, v) for t, v in by_t.items()))
        best_mean, best_target, best_runs = ranked[0]
        recs.append({
            "id": "REC-001-" + key[:12],
            "kind": "route_preference",
            "workload_key": key,
            "statement": (f"For recorded engineering workload {key[:12]} (<=3 input files), the observed "
                          f"fastest route is '{best_target}' at {best_mean:.3f}s mean over "
                          f"{len(best_runs)} run(s); the drafter's default remains direct_hearth."),
            "ranking": [{"target": t, "mean_duration_s": round(m, 3), "runs": [x["run_id"] for x in v],
                         "hosts": sorted({str(x.get('host')) for x in v}),
                         "models": sorted({str(x.get('model')) for x in v}),
                         "tokens": {"prompt": sum(int(x.get("prompt_tokens") or 0) for x in v),
                                    "completion": sum(int(x.get("completion_tokens") or 0) for x in v)}}
                        for m, t, v in ranked],
            "caveats": [
                "n is tiny; a single run per target is an observation, not a regime "
                "(one sample is not a regime).",
                "Durations are end-to-end from the operator's clock and include network and "
                "harness overhead, not decode rate alone.",
                "Matching recorded intent, criteria, inputs, classification and context limit "
                "does not verify source contents, equal result quality or current capacity.",
                "Completed/history-verified records are not independent semantic acceptance. "
                "Other workload groups must not be combined into this ranking.",
            ],
            "applied": False,
            "promotion_path": ("record a decision in the program repository; write "
                               "docs/adr/<next>-the-drafter-prefers-an-observed-route.md on the "
                               "ADR-0038 precedent (a verdict cites only evidence from the "
                               "configuration it promotes); accept through a gate recorded as a run; "
                               "only then may draft_route's default change"),
        })
    # 2. Every rejected first draft is a defect the drafter could avoid.
    rejected = [r for r in rows if (r.get("rejected_proposals") or 0) > 0]
    if rejected:
        recs.append({
            "id": "REC-002",
            "kind": "drafter_defect",
            "statement": (f"{len(rejected)} run(s) needed more than one proposal before one "
                          "validated; each rejection names a drafter assumption the validator "
                          "refused (context claim above the envelope, a target that was a rung "
                          "but not a LIVE implementation)."),
            "runs": [r["run_id"] for r in rejected],
            "applied": False,
            "promotion_path": "fix the drafter only for refusals actually observed; no test matrix",
        })
    # 3. Recovery locations: every artifact is still local_only.
    recs.append({
        "id": "REC-003",
        "kind": "durability",
        "statement": (f"{sum(1 for r in rows if r.get('reconstructable'))}/{len(rows)} runs replay "
                      "reconstructable, but no artifact has a verified recovery location "
                      "(recovery_locations_verified = 0 everywhere); D-004 second copy is not "
                      "wired into the artifact store."),
        "applied": False,
        "promotion_path": "D-004 / D-114: wire the second-copy location into artifacts.py; a gate run proves one restore",
    })
    return recs


def build_report(include_test_mode: bool = False) -> dict:
    rows = _collect_runs(include_test_mode=include_test_mode)
    by_target = _group(rows, "target")
    comparisons = _comparable_workloads(rows)
    comparable_count = sum(len({r["target"] for r in group}) >= 2
                           for group in comparisons.values())
    report = {
        "contract_version": CONTRACT_VERSION,
        "policy": {"excludes_test_mode": not include_test_mode,
                   "applies_nothing": True,
                   "promotion_requires": ["decision", "adr", "acceptance_gate_recorded_as_run"]},
        "runs_considered": len(rows),
        "runs": rows,
        "route_comparison": {
            "basis": "Exact recorded intent, criteria, inputs, classification and context limit; not content or quality proof.",
            "eligible_runs": sum(map(len, comparisons.values())),
            "workload_groups": len(comparisons),
            "compared_workload_groups": comparable_count,
            "reason": None if comparable_count else "no_matching_workload_across_multiple_targets",
            "mixed_aggregates_are_not_route_rankings": True,
        },
        "by_target": by_target,
        "by_host": _group(rows, "host"),
        "by_model": _group(rows, "model"),
        "by_task_type": _group(rows, "task_type"),
        "by_route_kind": _group(rows, "route_kind"),
        "recommendations": _recommendations(rows, by_target),
    }
    return report


def write_report(report: dict, *, json_path: Optional[Path] = None,
                 html_path: Optional[Path] = None) -> tuple[Path, Path]:
    root = paths.operator_home()
    json_path = json_path or (root / "knowledge" / "operator_learning.json")
    html_path = html_path or (root / "OPERATOR-LEARNING.html")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                         encoding="utf-8", newline="\n")
    html_path.write_text(render_html(report), encoding="utf-8", newline="\n")
    return json_path, html_path


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def render_html(report: dict) -> str:
    rows = report["runs"]
    comparison = report.get("route_comparison", {})
    out = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<title>Operator Learning</title>",
        "<meta name=\"description\" content=\"Offline learning report over runs/operator; recommends only, applies nothing.\">",
        "<style>:root{--bg:#fbfbf9;--fg:#1c1c1a;--muted:#6b6b66;--line:#e2e2dc;--accent:#8a4b08;--ok:#2f6b3a;--warn:#9a5b00}"
        "@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#141412;--fg:#e8e6df;--muted:#9a9891;--line:#2c2c28;--accent:#e0a458;--ok:#7fc48a;--warn:#e4b15a}}"
        ":root[data-theme=dark]{--bg:#141412;--fg:#e8e6df;--muted:#9a9891;--line:#2c2c28;--accent:#e0a458;--ok:#7fc48a;--warn:#e4b15a}"
        "body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,Segoe UI,sans-serif;max-width:1100px;margin-inline:auto}"
        "h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.1rem;margin:28px 0 8px;color:var(--accent)}"
        "table{border-collapse:collapse;width:100%;font-size:.9rem;overflow-x:auto;display:block}"
        "th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top;white-space:nowrap}"
        "code{font-family:ui-monospace,Consolas,monospace;font-size:.85em}.muted{color:var(--muted)}"
        ".rec{border:1px solid var(--line);border-left:4px solid var(--warn);padding:10px 14px;margin:10px 0;border-radius:6px}"
        ".rec b{color:var(--accent)}.ok{color:var(--ok)}ul{margin:6px 0 0 18px}</style></head><body>",
        "<h1>Operator Learning</h1>",
        "<p class=muted>Historical observations, not current capacity. Mixed task aggregates below "
        "are not route rankings. Like-for-like recorded workload groups compared: "
        f"{_esc(comparison.get('compared_workload_groups', 'unknown'))}. "
        f"{_esc(comparison.get('reason') or '')}</p>",
        f"<p class=muted>{_esc(report['contract_version'])} &middot; {report['runs_considered']} run(s) considered"
        f" &middot; test_mode {'excluded' if report['policy']['excludes_test_mode'] else 'included'}"
        " &middot; <b>recommends only; applies nothing</b> (promotion = decision + ADR + acceptance gate recorded as a run)</p>",
        "<h2>Recommendations</h2>",
    ]
    for rec in report["recommendations"]:
        out.append(f"<div class=rec><b>{_esc(rec['id'])}</b> <span class=muted>{_esc(rec['kind'])} &middot; applied: {str(rec['applied']).lower()}</span>"
                   f"<div>{_esc(rec['statement'])}</div>")
        if rec.get("ranking"):
            out.append("<table><tr><th>target</th><th>mean s</th><th>hosts</th><th>models</th><th>tokens in/out</th><th>runs</th></tr>")
            for r in rec["ranking"]:
                out.append(f"<tr><td><code>{_esc(r['target'])}</code></td><td>{r['mean_duration_s']}</td><td>{_esc(', '.join(r['hosts']))}</td>"
                           f"<td>{_esc(', '.join(r['models']))}</td><td>{r['tokens']['prompt']}/{r['tokens']['completion']}</td><td><code>{_esc(', '.join(r['runs']))}</code></td></tr>")
            out.append("</table>")
        if rec.get("caveats"):
            out.append("<ul>" + "".join(f"<li>{_esc(c)}</li>" for c in rec["caveats"]) + "</ul>")
        out.append(f"<div class=muted>promotion path: {_esc(rec['promotion_path'])}</div></div>")
    out.append("<h2>Runs</h2><table><tr><th>run</th><th>status</th><th>target</th><th>host</th><th>model</th><th>duration s</th><th>tokens in/out</th><th>task</th><th>attempts</th><th>rejected drafts</th><th>replay</th></tr>")
    for r in rows:
        out.append(f"<tr><td><code>{_esc(r['run_id'])}</code></td><td>{_esc(r['status'])}</td><td><code>{_esc(r['target'])}</code></td>"
                   f"<td>{_esc(r['host'])}</td><td>{_esc(r['model'])}</td><td>{_esc(r['duration_s'])}</td>"
                   f"<td>{_esc(r['prompt_tokens'])}/{_esc(r['completion_tokens'])}</td><td>{_esc(r['task_type'])}</td>"
                   f"<td>{r['attempts']}</td><td>{r['rejected_proposals']}</td>"
                   f"<td class={'ok' if r['reconstructable'] else 'muted'}>{'reconstructable' if r['reconstructable'] else 'auditable only'}</td></tr>")
    out.append("</table>")
    for title, key in (("By target", "by_target"), ("By host", "by_host"), ("By model", "by_model"), ("By task type", "by_task_type")):
        out.append(f"<h2>{title}</h2><table><tr><th>group</th><th>runs</th><th>completed</th><th>reconstructable</th><th>duration s (min/mean/max)</th><th>tokens in/out</th></tr>")
        for name, g in report[key].items():
            d = g["duration_s"]
            dur = f"{d['min']}/{d['mean']}/{d['max']}" if d else "-"
            out.append(f"<tr><td><code>{_esc(name)}</code></td><td>{g['runs']}</td><td>{g['completed']}</td><td>{g['reconstructable']}</td><td>{dur}</td><td>{g['prompt_tokens']}/{g['completion_tokens']}</td></tr>")
        out.append("</table>")
    out.append("<p class=muted>Generated deterministically from runs/operator by hearth.operator.learn; a projection, never truth. Do not hand-edit this file.</p></body></html>\n")
    return "\n".join(out)

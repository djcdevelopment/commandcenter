#!/usr/bin/env python3
"""Summarize recorded bench27 thinking runs; never dispatch, grade, or amend evidence.

Example: python tools/ops/bench27_summarize.py --root EVIDENCE --cards cards.jsonl --out NEW_DIRECTORY
Only a previously nonexistent output directory is accepted. Temperature matching is
inclusive UTC start/end; missing samples remain unknown. The A/A threshold is an
exploratory relative spread, not statistical significance or a substance verdict.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
from typing import Any

SCHEMA = "bench27-summary.v1"
METRICS = ("seconds_total", "work_seconds", "final_seconds", "decode_tokens_per_s")
CONTROLS = ("model", "temperature", "seed", "top_p", "max_tokens", "chat_template_kwargs")


class SummaryError(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> str:
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("expected an object")
        return value
    except (OSError, ValueError) as exc:
        raise SummaryError(f"{path}: {exc}") from exc


def utc(value: str) -> datetime:
    if not isinstance(value, str):
        raise SummaryError("timestamp must be an ISO string")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise SummaryError(f"timestamp lacks timezone: {value}")
    return result


def number(value: Any) -> bool:
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def spread(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "median": None, "min": None, "max": None, "relative_range": None}
    median = statistics.median(values)
    return {"n": len(values), "median": median, "min": min(values), "max": max(values),
            "relative_range": (max(values) - min(values)) / abs(median) if median else None}


def divergence(reference: str | None, current: str | None) -> dict:
    if reference is None or current is None:
        return {"state": "missing_artifact"}
    if reference == current:
        return {"state": "equal", "first_character": None}
    index = next((i for i, (a, b) in enumerate(zip(reference, current)) if a != b), min(len(reference), len(current)))
    return {"state": "different", "first_character": index, "line": reference[:index].count("\n") + 1,
            "column": index - reference.rfind("\n", 0, index),
            "reference_excerpt": reference[max(0, index - 24):index + 48],
            "current_excerpt": current[max(0, index - 24):index + 48]}


def read_thermal(paths: list[Path]) -> list[dict]:
    rows = []
    for path in paths:
        with path.open(encoding="utf-8") as source:
            for line_no, line in enumerate(source, 1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                    instant = utc(item["utc"])
                    rows.append({"time": instant, "row": item, "source": str(path), "line": line_no})
                except (ValueError, KeyError, TypeError) as exc:
                    raise SummaryError(f"thermal {path}:{line_no}: {exc}") from exc
    return rows


def thermal_window(rows: list[dict], start: str | None, end: str | None, card: str | None) -> dict:
    if not start or not end or card is None:
        return {"state": "unknown", "reason": "no timestamps or seat-to-card mapping", "samples": 0}
    selected = [r for r in rows if utc(start) <= r["time"] <= utc(end) and isinstance(r["row"].get(card), dict)]
    result = {"state": "observed" if selected else "unknown", "card": card, "samples": len(selected),
              "first_utc": selected[0]["row"]["utc"] if selected else None,
              "last_utc": selected[-1]["row"]["utc"] if selected else None,
              "sources": sorted({r["source"] for r in selected})}
    for key in ("vram_c", "pkg_c", "fan_rpm"):
        values = [r["row"][card][key] for r in selected if number(r["row"][card].get(key))]
        result[key] = spread(values)
    valid = sum(number(r["row"][card].get("vram_c")) for r in selected)
    result["invalid_samples"] = len(selected) - valid
    result["state"] = "observed" if selected and valid == len(selected) else "partial" if valid else "unknown"
    result["new_trips"] = [r["row"].get("new_trips") for r in selected if r["row"].get("new_trips")]
    return result


def stage_summary(directory: Path, stage: str, record: dict | None) -> tuple[dict, str | None]:
    if not isinstance(record, dict):
        return {"state": "missing"}, None
    observed = record.get("observed") or {}
    output = directory / "artifacts" / f"{stage}.output.txt"
    wire = directory / "artifacts" / f"{stage}.wire_request.txt"
    raw = output.read_bytes() if output.is_file() else None
    declared = (record.get("artifacts", {}).get("output") or {}).get("sha256")
    controls = read_json(wire) if wire.is_file() else {}
    result = {"job_id": record.get("job_id"), "status": record.get("status"), "reason": record.get("reason"), "seconds": record.get("seconds"),
              "observed": {key: observed.get(key) for key in (
                  "duration_ms", "tokens_in", "tokens_out", "tokens_reasoning", "thinking", "finish_reason",
                  "first_reasoning_ms", "first_content_ms", "max_tokens_requested", "max_tokens_applied", "model", "error_code")},
              "controls": {key: controls[key] for key in CONTROLS if key in controls}, "wire_recorded": wire.is_file(),
              "wire_equal": record.get("wire_equal"), "wire_mismatch": record.get("wire_mismatch"),
              "output_sha256": digest(raw) if raw is not None else None,
              "output_hash_matches": digest(raw) == declared if raw is not None and declared else None}
    first = [observed[k] for k in ("first_reasoning_ms", "first_content_ms") if number(observed.get(k))]
    duration, tokens = observed.get("duration_ms"), observed.get("tokens_out")
    result["decode_tokens_per_s_estimate"] = (tokens * 1000 / (duration - min(first))
        if number(tokens) and number(duration) and first and duration > min(first) else None)
    result["output_tokens_per_s"] = tokens * 1000 / duration if number(tokens) and number(duration) and duration > 0 else None
    return result, raw.decode("utf-8") if raw is not None else None


def collect(path: Path, root: Path, thermal: list[dict], cards: dict[str, str]) -> tuple[dict, dict]:
    run = read_json(path)
    if run.get("schema") != "thinking-run.v1":
        raise SummaryError(f"unsupported run schema: {path}")
    seat = next((p.name for p in path.parents if re.fullmatch(r"seat-\d+", p.name)), run.get("backend", "unknown"))
    stack = run.get("stack") or {}
    # Preserve provenance, never infer actual recipe equivalence from a model label.
    recipe = {key: run.get(key) for key in ("declared_models", "model_override", "declared_context_tokens", "observed_context_tokens")}
    recipe["stack_status"] = stack.get("status", "recorded" if stack.get("packages") else "unknown")
    recipe["stack_reason"] = stack.get("reason")
    recipe["packages"] = stack.get("packages")
    recipe["file_sha256"] = stack.get("file_sha256")
    delta = run.get("seat_counters_delta") or {}
    generation, decode = delta.get("generation_tokens_total"), delta.get("decode_seconds_sum")
    row = {"path": str(path.relative_to(root)), "run_sha256": digest(path.read_bytes()), "seat": seat,
           "repeat": str(path.parent.parent.relative_to(root)), "backend": run.get("backend"), "arm": run.get("arm"),
           "started_utc": run.get("started_utc"), "finished_utc": run.get("finished_utc"),
           "workload": run.get("workload"), "registry_sha256": run.get("registry_sha256"),
           "recipe": recipe, "recipe_sha256": canonical(recipe), "concurrency": run.get("concurrency"),
           "ok": run.get("ok"), "dry_run": run.get("dry_run"), "context_mismatch": run.get("context_mismatch"),
           "wire_equal_all": run.get("wire_equal_all"), "foreign_requests_possible": run.get("foreign_requests_possible"),
           "preemptions": delta.get("num_preemptions_total"), "seat_counters_delta": delta,
           "seconds_total": run.get("seconds_total"),
           "decode_tokens_per_s": generation / decode if number(generation) and number(decode) and decode > 0 else None,
           "thermal": thermal_window(thermal, run.get("started_utc"), run.get("finished_utc"), cards.get(seat)),
           "conversations": [], "timing_exclusions": []}
    texts = {}
    for conversation in run.get("conversations", []):
        identifier = conversation["conversation"]
        item = {"conversation": identifier, "ok": conversation.get("ok"), "failure": conversation.get("failure"),
                "final_skipped": conversation.get("final_skipped"), "seconds_total": conversation.get("seconds_total")}
        for stage in ("work", "final"):
            item[stage], texts[(identifier, stage)] = stage_summary(path.parent / f"conversation-{identifier}", stage, conversation.get(stage))
        row["conversations"].append(item)
    # Stage totals are wall durations only for the single-conversation A/A regime.
    for stage in ("work", "final"):
        values = [c[stage].get("seconds") for c in row["conversations"]]
        row[f"{stage}_seconds"] = values[0] if len(values) == 1 and number(values[0]) else None
    for key, expected in (("ok", True), ("dry_run", False), ("context_mismatch", False),
                          ("wire_equal_all", True), ("foreign_requests_possible", 0), ("preemptions", 0), ("concurrency", 1)):
        if row[key] != expected or row[key] is None:
            row["timing_exclusions"].append(f"{key}={row[key]!r}")
    if not isinstance((row["workload"] or {}).get("sha256"), str):
        row["timing_exclusions"].append("missing workload hash")
    if any(c["ok"] is not True or c[s].get("status") != "succeeded" or c[s].get("wire_equal") is not True
           or c[s].get("wire_recorded") is not True for c in row["conversations"] for s in ("work", "final")):
        row["timing_exclusions"].append("incomplete, failed, or wire-mismatched conversation")
    if len(row["conversations"]) != 1:
        row["timing_exclusions"].append("A/A calibration requires one conversation per run")
    if any(c[s].get("output_hash_matches") is not True for c in row["conversations"] for s in ("work", "final")):
        row["timing_exclusions"].append("missing or mismatched visible output artifact")
    return row, texts


def calibrate(rows: list[dict], reference_seat: str, minimum: int) -> dict:
    eligible = [row for row in rows if not row["timing_exclusions"]]
    reasons = []
    controls = [{"workload": (r["workload"] or {}).get("sha256"), "declared_recipe": {k: r["recipe"][k] for k in ("declared_models", "model_override", "declared_context_tokens", "observed_context_tokens")},
                 "regime": [{s: c[s]["controls"] for s in ("work", "final")} for c in r["conversations"]]} for r in eligible]
    if len({canonical(c) for c in controls}) > 1:
        reasons.append("workload, declared model/context, or generation controls differ; separate the regimes")
    snapshots = [r["recipe"] for r in eligible if r["recipe"].get("packages") and r["recipe"].get("file_sha256")]
    snapshot_hashes = {canonical({k: p[k] for k in ("packages", "file_sha256")}) for p in snapshots}
    recipe_state = ("different_recorded_snapshots" if len(snapshot_hashes) > 1 else
                    "matching_recorded_snapshots" if len(snapshots) == len(eligible) and eligible else "incomplete_per_run_snapshots")
    if len(snapshot_hashes) > 1:
        reasons.append("recorded recipe snapshots differ; separate the regimes")
    seats = sorted({r["seat"] for r in rows})
    if len(seats) != 2 or reference_seat not in seats:
        reasons.append("A/A requires exactly two seats including the reference seat")
    metrics = {}
    for metric in METRICS:
        by_seat = {seat: spread([r[metric] for r in eligible if r["seat"] == seat and number(r.get(metric))]) for seat in seats}
        complete = len(seats) == 2 and all(s["n"] >= minimum and s["relative_range"] is not None for s in by_seat.values())
        medians = [s["median"] for s in by_seat.values() if s["median"] is not None]
        between = abs(medians[0] - medians[1]) / statistics.mean(map(abs, medians)) if len(medians) == 2 and any(medians) else None
        within = [s["relative_range"] for s in by_seat.values() if s["relative_range"] is not None]
        estimate = max([between, *within]) if between is not None and within and not reasons else None
        metrics[metric] = {"seats": by_seat, "between_relative_median_difference": between,
                           "threshold": estimate if complete and not reasons else None,
                           "provisional_estimate": estimate, "ready": complete and not reasons}
    return {"minimum_repetitions_per_seat": minimum, "reasons": reasons, "metrics": metrics,
            "recipe_comparability": recipe_state,
            "formula": "max((max-min)/abs(median) within each seat, abs(median0-median1)/mean(abs(median0),abs(median1)))",
            "interpretation": "exploratory timing spread only; no statistical significance or semantic grade"}


def summarize(root: Path, cards_files: list[Path] | None = None, *, reference_seat: str = "seat-0",
              card_map: dict[str, str] | None = None, minimum: int = 3, provenance: list[Path] | None = None) -> dict:
    root = root.resolve()
    paths = sorted(root.rglob("run.json"))
    if not paths:
        raise SummaryError(f"no run.json found under {root}")
    thermal = sorted(read_thermal(cards_files or []), key=lambda r: r["time"])
    pairs = []
    for path in paths:
        try:
            pairs.append(collect(path, root, thermal, card_map or {"seat-0": "card2", "seat-1": "card3"}))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SummaryError(f"cannot summarize {path}: {exc}") from exc
    pairs.sort(key=lambda pair: (utc(pair[0]["started_utc"]) if pair[0].get("started_utc") else datetime.min.replace(tzinfo=timezone.utc), pair[0]["path"]))
    baseline = next((pair for pair in pairs if pair[0]["seat"] == reference_seat), None)
    if baseline is None:
        raise SummaryError(f"reference seat {reference_seat!r} not found")
    for row, texts in pairs:
        for conversation in row["conversations"]:
            identifier = conversation["conversation"]
            for stage in ("work", "final"):
                conversation[stage]["divergence"] = divergence(baseline[1].get((identifier, stage)), texts.get((identifier, stage)))
    rows = [pair[0] for pair in pairs]
    return {"schema": SCHEMA, "root": str(root), "reference_run": baseline[0]["path"], "reference_seat": reference_seat,
            "runs": rows, "calibration": calibrate(rows, reference_seat, minimum),
            "external_provenance": [{"path": str(p.resolve()), "sha256": digest(p.read_bytes())} for p in (provenance or [])],
            "limits": ["No substance or form grades. Visible text comparison is character-exact, not token divergence.",
                       "Stage decode rates are estimates excluding time to first reasoning/content; seat decode is counter-derived.",
                       "Thermal samples are matched only inside each run's timestamps; missing coverage is unknown.",
                       "Recipe hashes describe recorded snapshots, not independently verified effective hardware flags.",
                       "External provenance records are linked and hashed; binding entry configuration to each run requires caller review."]}


def markdown(summary: dict) -> str:
    lines = ["# Bench27 recorded-run summary", "", f"Reference: `{summary['reference_run']}`. No semantic grades.", "",
             "| Run | Seat | OK | Total s | Work s | Final s | Seat decode tok/s | Preemptions | Foreign | VRAM max C | Timing exclusions |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    def cell(value):
        return "unknown" if value is None else str(round(value, 4) if isinstance(value, float) else value).replace("|", "\\|").replace("\n", " ")
    for r in summary["runs"]:
        cells = [r["path"], r["seat"], r["ok"], r["seconds_total"], r["work_seconds"], r["final_seconds"],
                 r["decode_tokens_per_s"], r["preemptions"], r["foreign_requests_possible"],
                 r["thermal"].get("vram_c", {}).get("max"), "; ".join(r["timing_exclusions"]) or "none"]
        lines.append("| " + " | ".join(map(cell, cells)) + " |")
    lines += ["", "## A/A timing calibration", "", summary["calibration"]["formula"], "",
              "| Metric | Ready | Threshold | Provisional estimate | Per-seat median [min, max], n |", "|---|---|---:|---:|---|"]
    for key, metric in summary["calibration"]["metrics"].items():
        ranges = "; ".join(f"{seat}: {cell(s['median'])} [{cell(s['min'])}, {cell(s['max'])}], n={s['n']}" for seat, s in metric["seats"].items())
        lines.append("| " + " | ".join(map(cell, [key, metric["ready"], metric["threshold"], metric["provisional_estimate"], ranges])) + " |")
    lines += ["", *summary["calibration"]["reasons"], "", "## Workload and recorded recipe", "",
              "| Run | Workload | Workload SHA-256 | Context | Recipe snapshot SHA-256 |", "|---|---|---|---:|---|"]
    for row in summary["runs"]:
        workload = row["workload"] or {}
        lines.append("| " + " | ".join(map(cell, [row["path"], workload.get("id"), workload.get("sha256"),
                     row["recipe"]["observed_context_tokens"], row["recipe_sha256"]])) + " |")
    lines += ["", "## Stage measurements and regime", "",
              "| Run / conversation / stage | Input tokens | Output tokens | Reasoning tokens | Estimated decode tok/s | Temperature / seed / thinking | Output budget | Finish |",
              "|---|---:|---:|---:|---:|---|---:|---|"]
    for row in summary["runs"]:
        for c in row["conversations"]:
            for stage in ("work", "final"):
                info = c[stage]
                observed, controls = info.get("observed", {}), info.get("controls", {})
                regime = " / ".join(map(cell, [controls.get("temperature"), controls.get("seed"),
                                               controls.get("chat_template_kwargs", {}).get("enable_thinking")]))
                lines.append("| " + " | ".join(map(cell, [f"{row['path']} / {c['conversation']} / {stage}",
                    observed.get("tokens_in"), observed.get("tokens_out"), observed.get("tokens_reasoning"),
                    info.get("decode_tokens_per_s_estimate"), regime, controls.get("max_tokens"), observed.get("finish_reason")])) + " |")
    lines += ["", "## Visible-text divergence", ""]
    for row in summary["runs"]:
        for c in row["conversations"]:
            for stage in ("work", "final"):
                d = c[stage]["divergence"]
                lines.append(f"- `{row['path']}` conversation {c['conversation']} {stage}: {d['state']}"
                             + (f" at character {d['first_character']} (line {d['line']}, column {d['column']})." if d["state"] == "different" else "."))
    lines += ["", f"Per-run recipe comparison: {summary['calibration']['recipe_comparability']}.", ""]
    if summary["external_provenance"]:
        lines += ["External entry/configuration evidence (caller binds it to these runs):", ""]
        lines += [f"- [{Path(p['path']).name}]({p['path']}), SHA-256 `{p['sha256']}`." for p in summary["external_provenance"]]
    lines += ["", "## Interpretation limits", "", *[f"- {s}" for s in summary["limits"]], ""]
    return "\n".join(lines)


def write_summary(summary: dict, output: Path) -> None:
    # No overwrite flag: original runs and previous summaries stay untouched.
    encoded = json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n"
    report = markdown(summary)
    output.mkdir(parents=True, exist_ok=False)
    (output / "summary.json").write_text(encoded, encoding="utf-8")
    (output / "SUMMARY.md").write_text(report, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--cards", action="append", default=[], type=Path, help="thermal cards.jsonl; repeat for separate logs")
    parser.add_argument("--provenance-record", action="append", default=[], type=Path, help="entry argv/config evidence to link and hash, without asserting per-run binding")
    parser.add_argument("--out", required=True, type=Path, help="new output directory; existing paths refuse")
    parser.add_argument("--reference-seat", default="seat-0")
    parser.add_argument("--card-map", action="append", default=[], metavar="SEAT=CARD", help="override seat/card mapping; defaults seat-0=card2, seat-1=card3")
    parser.add_argument("--min-repeats", type=int, default=3)
    args = parser.parse_args()
    if args.min_repeats < 2:
        parser.error("--min-repeats must be at least two")
    cards = {"seat-0": "card2", "seat-1": "card3"}
    for mapping in args.card_map:
        if "=" not in mapping or not all(mapping.split("=", 1)):
            parser.error("--card-map requires SEAT=CARD")
        seat, card = mapping.split("=", 1)
        cards[seat] = card
    try:
        summary = summarize(args.root, args.cards, reference_seat=args.reference_seat, minimum=args.min_repeats, provenance=args.provenance_record, card_map=cards)
        write_summary(summary, args.out)
    except (SummaryError, OSError, ValueError) as exc:
        parser.exit(2, f"refused: {exc}\n")
    print(args.out / "summary.json")
    print(args.out / "SUMMARY.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
r"""SAT-L1 noise floor -- reduce the repeats of ONE cell into a spread, a CI and P7.

Spec: ``docs/prereg/SATURATION-SURFACE-LAP1.md`` (tag ``prereg-saturation-lap1-20260909``).
The Analysis plan names the noise floor of the whole surface:

    "Noise floor: the N=2 / 512 cell's >=5 repeats; a between-cell difference smaller than
     that floor is 'within noise', never a finding. CI: bootstrap over requests for latency,
     over repeats for jobs/hour."

and the Kill / pivot gate turns it into a stop condition:

    "Repeats drift beyond the noise floor at the N=2 / 512 cell after warm-up -> the warm
     gate is not holding; no reading from the block is valid; fix the gate, then resume."

This module is the reducer for that cell and nothing else. It is **pure and offline**: it
reads receipts written by ``sat_cell_runner.py``, it never touches the rung, never runs a
cell, never writes inside ``E:\work\battlemage``. The only thing it can write is the
optional ``--json-out`` document.

WHAT IT SCORES
  P7 (prediction table, ~90%): "Warm-then-measure holds cell repeats within the FF6 noise
  floors (1.5% pp512, 0.64% pp2048, 1.2% tg128); an unwarmed rep-1 reads 65-90% of warm
  (ADR-0043's 68/69/74/92)." That is TWO claims wearing one number, so they are scored as
  two halves and reported separately:

    1. ``repeat_spread``  -- the cell's jobs/hour spread against the pp512 floor. The floor
       is carried over from FF6, where it was a SINGLE-STREAM llama-bench spread; this cell
       is a CONCURRENT wall-clock jobs/hour over a handful of requests. The comparison is
       the card's own stated test, so it is made -- and the note on every result says the
       two are not like-for-like, because a 1.5% floor borrowed across measurement kinds is
       exactly the sort of number that later gets cited as if it had been measured here.

    2. ``unwarmed_rep1`` -- ``warm.unwarmed_rep1_tok_s / warm.final_decode_tok_s`` per
       repeat. Back-to-back cells never go idle, so this half is routinely **untested**
       rather than supported: a rung that was already warm at rep-1 cannot falsify a
       prediction about unwarmed rep-1s. ``untested`` is reported as its own outcome and is
       never quietly folded into "supported" -- the pass gate asks for *supported* or
       *refuted*, and an honest ``untested`` is the only way that gate stays meaningful.

  Neither half ever returns "unclear".

EXCLUSIONS ARE LISTED, NEVER DROPPED. A receipt is included only when ``status ==
"scored"`` and ``over_admitted`` is false -- the same rule the runner states for the surface
("an over_admitted row is KEPT and excluded from the surface, not dropped"). Every excluded
receipt appears in the report with its reason, as does every discovered directory that had
no readable receipt, so ``n`` is always reconcilable against what is on disk.

DISCOVERY is numeric, not lexical: ``<cells-root>/<prefix>-r<k>/receipt.json`` sorted by the
integer ``k``, so ``r10`` follows ``r9`` instead of ``r1``.

USAGE

    python campaign/ff-probes/sat_noise_floor.py \
        --cells-root E:\work\battlemage\sat-l1\cells --cell-prefix np2-p512-c2
    python campaign/ff-probes/sat_noise_floor.py --receipt A.json --receipt B.json

Exit 0 on success; exit 2 when fewer than two repeats survive the include rule (a "noise
floor" over one repeat is not a floor).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import statistics
import sys
from pathlib import Path

PROBE = "SAT-L1-NOISE-FLOOR"
SCHEMA_VERSION = 1

#: FF6's published noise floors, by test kind. Carried over, not re-measured here.
FF6_FLOORS = {"pp512": 1.5, "pp2048": 0.64, "tg128": 1.2}

#: The prereg asks for >=5 repeats at this cell before the floor is treated as the floor.
PREREG_MIN_REPEATS = 5

#: P7's unwarmed-rep-1 band, and the ratio at or above which a rep-1 was already warm.
UNWARMED_BAND = (0.65, 0.90)
ALREADY_WARM_RATIO = 0.95

#: Directory name -> repeat index. ``np2-p512-c2-r10`` sorts after ``np2-p512-c2-r2``.
REPEAT_RE = re.compile(r"-r(\d+)$")

#: (report name, path into the receipt, unit)
SCALAR_FIELDS = (
    ("jobs_per_hour", ("jobs_per_hour",), "jobs_per_hour"),
    ("latency_p50_s", ("latency_p50_s",), "seconds"),
    ("latency_p95_s", ("latency_p95_s",), "seconds"),
    ("latency_p99_s", ("latency_p99_s",), "seconds"),
    ("ttft_p50_s", ("ttft_p50_s",), "seconds"),
    ("decode_rate_p50_tokens_per_s", ("decode_rate_p50_tokens_per_s",), "tokens_per_s"),
    ("both_slots_busy_fraction", ("both_slots_busy_fraction",), "fraction"),
    ("slot_busy_fraction", ("slot_busy_fraction",), "fraction"),
    ("incumbent_rate_fraction_pre", ("incumbent_rate_fraction_pre",), "fraction"),
    ("incumbent_rate_fraction_post", ("incumbent_rate_fraction_post",), "fraction"),
    ("symmetry.ratio", ("symmetry", "ratio"), "ratio"),
)

#: (report label, path to the per-key mapping, path inside each entry, unit)
#: ``duty_cycle`` and ``budget_headroom`` are keyed by PCI BDF; ``power`` is keyed by the
#: b70tools adapter id. They are NOT the same key space and are never merged.
CARD_FAMILIES = (
    ("duty_cycle[%s]", ("duty_cycle", "cards"), ("duty_cycle",), "fraction"),
    ("power.burst_p50_w[%s]", ("power", "cards"), ("burst", "p50_w"), "watts"),
    ("min_headroom_gb[%s]", ("budget_headroom", "cards"), ("min_headroom_gb",), "gb"),
)

#: decimal places by unit; percentages are always 2.
ROUNDING = {
    "jobs_per_hour": 1,
    "seconds": 3,
    "tokens_per_s": 2,
    "fraction": 4,
    "ratio": 4,
    "gb": 3,
    "watts": 2,
    "percent": 2,
}


# --------------------------------------------------------------------- small helpers --
def dig(obj, path):
    """Walk ``path`` through nested dicts; ``None`` the moment a level is missing."""
    cur = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def is_number(value) -> bool:
    """True for a real number. ``bool`` is not a measurement, so it is excluded."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def repeat_id(receipt: dict, fallback: str = "<unnamed>") -> str:
    cell = receipt.get("cell")
    return cell if isinstance(cell, str) and cell else fallback


def repeat_index(name: str) -> int | None:
    """The integer after the trailing ``-r``, or ``None`` when there is none."""
    m = REPEAT_RE.search(name)
    return int(m.group(1)) if m else None


def round_unit(value, unit: str):
    if value is None:
        return None
    return round(float(value), ROUNDING.get(unit, 3))


def round_pct(value):
    if value is None:
        return None
    return round(float(value), ROUNDING["percent"])


def percentile(sorted_values: list, q: float):
    """Nearest-rank percentile, the convention the harness uses for its own p50/p95/p99."""
    if not sorted_values:
        return None
    rank = max(1, math.ceil(q * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


# ------------------------------------------------------------------- the include rule --
def include_reason(receipt: dict, *, include_cached: bool = False) -> str | None:
    """``None`` when the receipt belongs in the surface, else why it does not.

    The rule is the runner's: scored, and not over-admitted. An ``over_admitted`` row is
    real data about a real cell -- it is excluded from the surface and KEPT in the report.

    Regime (added 2026-09-09 after block 1): the surface is the REAL-PREFILL regime. A receipt
    is in it only when it says so -- top-level ``cache_prompt`` is ``False`` and the runner's
    gate 7 did not mark it ``prefill_cached``. Block-1 receipts (r1-r5 of np2-p512-c2) predate
    both fields: the load harness sent one identical prompt per request with the server's
    prompt cache on, so they are the cached-prefix regime and are excluded by default, never
    silently averaged in. ``include_cached`` reduces them on purpose.
    """
    status = receipt.get("status")
    if status != "scored":
        reason = receipt.get("status_reason")
        detail = " -- %s" % reason if isinstance(reason, str) and reason else ""
        return "status is %r, not 'scored'%s" % (status, detail)
    if receipt.get("over_admitted"):
        return "over_admitted: budget headroom went negative during the cell"
    if not include_cached:
        if receipt.get("prefill_cached"):
            return ("prefill_cached: gate 7 found the server served the prompts from cache; "
                    "cached-prefix regime, not the surface (pass --include-cached to reduce it)")
        if receipt.get("cache_prompt") is not False:
            return ("cached-prefix regime: receipt carries no cache_prompt:false (predates gate 7 "
                    "-- block-1 instrument-validation run); pass --include-cached to reduce it")
    return None


def partition(receipts: list, *, include_cached: bool = False) -> tuple:
    """Split into (included, excluded) preserving order; excluded rows carry a reason."""
    included, excluded = [], []
    for i, receipt in enumerate(receipts):
        rid = repeat_id(receipt, "receipt[%d]" % i)
        reason = include_reason(receipt, include_cached=include_cached)
        if reason is None:
            included.append(receipt)
        else:
            excluded.append({"repeat": rid, "reason": reason,
                             "status": receipt.get("status"),
                             "over_admitted": bool(receipt.get("over_admitted"))})
    return included, excluded


# ---------------------------------------------------------------------- the statistics --
def summarize(values: list, unit: str, missing: int) -> dict:
    """n / min / max / mean / median / spread% / cv% over the values that were present."""
    n = len(values)
    row = {"n": n, "missing": missing, "unit": unit, "skipped": False,
           "min": None, "max": None, "mean": None, "median": None,
           "spread_pct": None, "cv_pct": None}
    if n == 0:
        return row
    mean = statistics.fmean(values)
    row["min"] = round_unit(min(values), unit)
    row["max"] = round_unit(max(values), unit)
    row["mean"] = round_unit(mean, unit)
    row["median"] = round_unit(statistics.median(values), unit)
    if mean != 0:
        row["spread_pct"] = round_pct((max(values) - min(values)) / mean * 100.0)
        # population stddev: these repeats ARE the sample, not a draw from a larger one.
        row["cv_pct"] = round_pct(statistics.pstdev(values) / mean * 100.0)
    return row


def collect_field(receipts: list, path, unit: str) -> dict:
    """Pull one numeric field across repeats. A structured value skips the whole field."""
    values, missing, structured = [], 0, []
    for i, receipt in enumerate(receipts):
        raw = dig(receipt, path)
        if raw is None:
            missing += 1
        elif is_number(raw):
            values.append(float(raw))
        else:
            structured.append({"repeat": repeat_id(receipt, "receipt[%d]" % i),
                               "type": type(raw).__name__})
    if structured:
        where = ", ".join("%s (%s)" % (s["repeat"], s["type"]) for s in structured)
        return {"n": 0, "missing": missing, "unit": unit, "skipped": True,
                "skip_reason": "not a number in %s -- this reducer only spreads scalars"
                               % where,
                "min": None, "max": None, "mean": None, "median": None,
                "spread_pct": None, "cv_pct": None,
                "values": []}
    row = summarize(values, unit, missing)
    row["values"] = [round_unit(v, unit) for v in values]
    return row


def card_keys(receipts: list, mapping_path) -> list:
    """Every key seen across repeats, sorted -- a repeat missing one counts as missing."""
    keys = set()
    for receipt in receipts:
        mapping = dig(receipt, mapping_path)
        if isinstance(mapping, dict):
            keys.update(k for k in mapping if isinstance(k, str))
    return sorted(keys)


def collect_fields(receipts: list) -> dict:
    """The whole per-field table, scalars first, then the per-card families in order."""
    fields = {}
    for name, path, unit in SCALAR_FIELDS:
        fields[name] = collect_field(receipts, path, unit)
    for label, mapping_path, inner_path, unit in CARD_FAMILIES:
        for key in card_keys(receipts, mapping_path):
            fields[label % key] = collect_field(
                receipts, tuple(mapping_path) + (key,) + tuple(inner_path), unit)
    return fields


def bootstrap_ci(values: list, *, seed: int, resamples: int, conf: float = 0.95) -> dict:
    """Seeded percentile bootstrap CI of the MEAN over repeats. Deterministic per seed."""
    n = len(values)
    doc = {"stat": "mean", "over": "repeats", "n": n, "seed": seed,
           "resamples": resamples, "conf": conf,
           "mean": None, "lo": None, "hi": None, "reason": None}
    if n < 2:
        doc["reason"] = "fewer than 2 repeats -- a CI over one reading is not a CI"
        return doc
    if resamples < 1:
        doc["reason"] = "resamples < 1"
        return doc
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    tail = (1.0 - conf) / 2.0
    doc["mean"] = statistics.fmean(values)
    doc["lo"] = percentile(means, tail)
    doc["hi"] = percentile(means, 1.0 - tail)
    return doc


# ------------------------------------------------------------------------ the P7 halves --
def score_repeat_spread(fields: dict, floor_pct: float, requests_seen: list) -> dict:
    """P7 half 1: does the cell's jobs/hour spread sit inside the borrowed FF6 floor?"""
    jph = fields.get("jobs_per_hour", {})
    p95 = fields.get("latency_p95_s", {})
    spread = jph.get("spread_pct")
    jobs = "/".join(str(r) for r in requests_seen) if requests_seen else "an unrecorded number of"
    note = ("the FF6 floors are SINGLE-STREAM llama-bench spreads (%.2f%% pp512); this cell "
            "is a CONCURRENT wall-clock jobs/hour over %s request(s) at N=2. The comparison "
            "is made because the card states it, but it is the card's own stated test, not a "
            "like-for-like measurement -- do not re-cite this floor as if it were measured "
            "on the concurrent wall."
            % (floor_pct, jobs))
    doc = {"half": "repeat_spread", "metric": "jobs_per_hour.spread_pct",
           "floor_pct": round_pct(floor_pct), "floor_source": "FF6 pp512",
           "observed_spread_pct": spread, "n": jph.get("n", 0),
           "latency_p95_spread_pct": p95.get("spread_pct"),
           "within_floor": None, "outcome": None, "note": note}
    if spread is None:
        doc["outcome"] = "untested"
        doc["reason"] = "no jobs_per_hour spread available (n=%d)" % jph.get("n", 0)
        return doc
    doc["within_floor"] = bool(spread <= floor_pct)
    doc["outcome"] = "supported" if doc["within_floor"] else "refuted"
    return doc


def score_unwarmed_rep1(receipts: list) -> dict:
    """P7 half 2: unwarmed rep-1 as a fraction of the warm rate, per repeat.

    ADR-0043's band is 65-90%. A ratio at or above 0.95 means the rung was never cold, so
    the repeat cannot test the claim; when EVERY repeat is like that the half is
    ``untested`` -- back-to-back cells are the normal case, and calling that "supported"
    would manufacture evidence out of the absence of an idle period.
    """
    lo, hi = UNWARMED_BAND
    ratios = []
    for i, receipt in enumerate(receipts):
        rid = repeat_id(receipt, "receipt[%d]" % i)
        unwarmed = dig(receipt, ("warm", "unwarmed_rep1_tok_s"))
        warm = dig(receipt, ("warm", "final_decode_tok_s"))
        entry = {"repeat": rid, "unwarmed_rep1_tok_s": unwarmed,
                 "final_decode_tok_s": warm, "ratio": None, "in_band": None,
                 "already_warm": None}
        if is_number(unwarmed) and is_number(warm) and float(warm) != 0:
            ratio = float(unwarmed) / float(warm)
            entry["ratio"] = round(ratio, 4)
            entry["already_warm"] = bool(ratio >= ALREADY_WARM_RATIO)
            entry["in_band"] = bool(lo <= ratio <= hi)
        ratios.append(entry)

    scored = [r for r in ratios if r["ratio"] is not None]
    doc = {"half": "unwarmed_rep1", "band": [lo, hi],
           "already_warm_at_or_above": ALREADY_WARM_RATIO,
           "ratios": ratios, "outcome": None, "reason": None}
    if not scored:
        doc["outcome"] = "untested"
        doc["reason"] = ("no repeat carried both warm.unwarmed_rep1_tok_s and "
                         "warm.final_decode_tok_s")
        return doc
    cold = [r for r in scored if not r["already_warm"]]
    if not cold:
        doc["outcome"] = "untested"
        doc["reason"] = ("rung was already warm at every repeat (back-to-back cells; no idle "
                         "before measurement)")
        return doc
    doc["outcome"] = "supported" if all(r["in_band"] for r in cold) else "refuted"
    doc["reason"] = ("%d of %d repeat(s) started cold; %s within the %.2f-%.2f band"
                     % (len(cold), len(scored),
                        "all" if doc["outcome"] == "supported" else "not all", lo, hi))
    return doc


# --------------------------------------------------------------------- the reduction --
def reduce_repeats(receipts: list, *, floors: dict | None = None,
                   seed: int = 20260909, resamples: int = 10000,
                   include_cached: bool = False) -> dict:
    """Pure reduction over receipt dicts. No I/O, no clock, no globals mutated."""
    floors = dict(FF6_FLOORS if floors is None else floors)
    floor_pct = float(floors.get("pp512", FF6_FLOORS["pp512"]))

    included, excluded = partition(receipts, include_cached=include_cached)
    fields = collect_fields(included)

    jph_values = [v for v in (dig(r, ("jobs_per_hour",)) for r in included) if is_number(v)]
    ci = bootstrap_ci([float(v) for v in jph_values], seed=seed, resamples=resamples)

    guards = []
    for i, receipt in enumerate(included):
        guards.append({"repeat": repeat_id(receipt, "receipt[%d]" % i),
                       "guard_before": dig(receipt, ("guard_before", "verdict")),
                       "guard_after": dig(receipt, ("guard_after", "verdict"))})
    all_at_rate = bool(guards) and all(
        g["guard_before"] == "at_rate" and g["guard_after"] == "at_rate" for g in guards)

    commits = []
    for receipt in included:
        commit = receipt.get("runner_commit")
        if commit is not None and commit not in commits:
            commits.append(commit)

    requests_seen = []
    for receipt in included:
        req = receipt.get("load_requests")
        if is_number(req) and req not in requests_seen:
            requests_seen.append(int(req))

    return {
        "probe": PROBE,
        "schema_version": SCHEMA_VERSION,
        "n_included": len(included),
        "n_excluded": len(excluded),
        "regime_filter": ("cached receipts included on request" if include_cached
                          else "real-prefill regime only (cache_prompt:false, not prefill_cached)"),
        "repeats": [repeat_id(r, "receipt[%d]" % i) for i, r in enumerate(included)],
        "excluded": excluded,
        "prereg_min_repeats": PREREG_MIN_REPEATS,
        "meets_prereg_repeats": len(included) >= PREREG_MIN_REPEATS,
        "regime": included[0].get("regime") if included else None,
        "runner_commits": commits,
        "mixed_runner_commits": len(commits) > 1,
        "load_requests_seen": requests_seen,
        "floors": floors,
        "fields": fields,
        "bootstrap": {"jobs_per_hour": ci},
        "guards": {"rows": guards, "all_at_rate": all_at_rate},
        "p7": {
            "prediction": ("warm-then-measure holds cell repeats within the FF6 noise floors "
                           "(1.5% pp512, 0.64% pp2048, 1.2% tg128); an unwarmed rep-1 reads "
                           "65-90% of warm"),
            "repeat_spread": score_repeat_spread(fields, floor_pct, requests_seen),
            "unwarmed_rep1": score_unwarmed_rep1(included),
        },
    }


# ------------------------------------------------------------------------ the markdown --
def _cell(value) -> str:
    """Render an already-rounded number; ``str`` keeps the 1 dp on 2334.0."""
    return "null" if value is None else str(value)


def render_markdown(doc: dict, *, cell_prefix: str, skipped: list | None = None) -> str:
    out = []
    add = out.append
    red = doc["reduction"] if "reduction" in doc else doc
    skipped = skipped or []

    add("# SAT-L1 noise floor -- %s" % cell_prefix)
    add("")
    add("- repeats included: **%d**%s"
        % (red["n_included"],
           (" (%s)" % ", ".join(red["repeats"])) if red["repeats"] else ""))
    add("- repeats excluded: **%d**" % red["n_excluded"])
    if skipped:
        add("- directories skipped before parsing: **%d**" % len(skipped))
    regime = red.get("regime") or {}
    if isinstance(regime, dict) and regime:
        add("- regime: model %s %s, depth %s, N=%s, -np %s, placement %s, ctx %s"
            % (regime.get("model"), regime.get("quant"), regime.get("depth_tokens"),
               regime.get("concurrency"), regime.get("np"), regime.get("placement"),
               regime.get("ctx")))
    commits = red.get("runner_commits") or []
    add("- runner_commit seen: %s%s"
        % (", ".join(str(c) for c in commits) if commits else "none recorded",
           "  **MIXED -- the repeats were not produced by one build**"
           if red.get("mixed_runner_commits") else ""))
    if not red.get("meets_prereg_repeats"):
        add("- **the pre-registration asks for >=%d repeats at this cell; n=%d. This floor is "
            "provisional.**" % (red["prereg_min_repeats"], red["n_included"]))
    add("")

    add("## Exclusions")
    add("")
    if not red["excluded"] and not skipped:
        add("_none -- every discovered receipt is in the surface._")
    else:
        for row in red["excluded"]:
            add("- `%s` -- %s" % (row["repeat"], row["reason"]))
        for row in skipped:
            add("- `%s` -- %s" % (row.get("path", "?"), row.get("reason", "?")))
    add("")

    add("## Per-field spread across repeats")
    add("")
    add("| field | n | min | max | mean | spread % | cv % |")
    add("|---|---:|---:|---:|---:|---:|---:|")
    for name, row in red["fields"].items():
        if row.get("skipped"):
            add("| `%s` | 0 | null | null | null | null | SKIPPED: %s |"
                % (name, row.get("skip_reason", "not a scalar")))
            continue
        missing = "" if not row.get("missing") else " (%d missing)" % row["missing"]
        add("| `%s` | %d%s | %s | %s | %s | %s | %s |"
            % (name, row["n"], missing, _cell(row["min"]), _cell(row["max"]),
               _cell(row["mean"]), _cell(row["spread_pct"]), _cell(row["cv_pct"])))
    add("")

    ci = red["bootstrap"]["jobs_per_hour"]
    add("## Bootstrap CI")
    add("")
    if ci.get("lo") is None:
        add("- jobs/hour mean CI: **not computed** -- %s" % ci.get("reason"))
    else:
        add("- jobs/hour mean **%.1f**, %d%% CI **[%.1f, %.1f]** "
            "(percentile bootstrap over %d repeats, seed %d, %d resamples)"
            % (ci["mean"], round(ci["conf"] * 100), ci["lo"], ci["hi"],
               ci["n"], ci["seed"], ci["resamples"]))
    add("")

    guards = red["guards"]
    add("## Production guard")
    add("")
    add("| repeat | guard_before | guard_after |")
    add("|---|---|---|")
    for row in guards["rows"]:
        add("| `%s` | %s | %s |" % (row["repeat"], row["guard_before"], row["guard_after"]))
    add("")
    add("- all_at_rate: **%s**" % ("yes" if guards["all_at_rate"] else "NO"))
    add("")

    p7 = red["p7"]
    add("## P7 -- warm-then-measure")
    add("")
    add("> %s" % p7["prediction"])
    add("")

    rs = p7["repeat_spread"]
    add("### half 1 -- repeat spread: **%s**" % rs["outcome"].upper())
    add("")
    add("- `jobs_per_hour` spread **%s%%** vs the %s floor **%s%%** -> within_floor: **%s**"
        % (_cell(rs["observed_spread_pct"]), rs["floor_source"], _cell(rs["floor_pct"]),
           "yes" if rs["within_floor"] else ("n/a" if rs["within_floor"] is None else "NO")))
    add("- `latency_p95_s` spread: **%s%%**" % _cell(rs["latency_p95_spread_pct"]))
    if rs.get("reason"):
        add("- %s" % rs["reason"])
    add("- NOTE: %s" % rs["note"])
    add("")

    uw = p7["unwarmed_rep1"]
    add("### half 2 -- unwarmed rep-1: **%s**" % uw["outcome"].upper())
    add("")
    add("- band %.2f-%.2f; a ratio >= %.2f means the rung was already warm and the repeat "
        "cannot test the claim" % (uw["band"][0], uw["band"][1],
                                   uw["already_warm_at_or_above"]))
    add("")
    add("| repeat | unwarmed rep-1 tok/s | warm tok/s | ratio | already warm | in band |")
    add("|---|---:|---:|---:|---|---|")
    for row in uw["ratios"]:
        add("| `%s` | %s | %s | %s | %s | %s |"
            % (row["repeat"], _cell(row["unwarmed_rep1_tok_s"]),
               _cell(row["final_decode_tok_s"]), _cell(row["ratio"]),
               "null" if row["already_warm"] is None else ("yes" if row["already_warm"] else "no"),
               "null" if row["in_band"] is None else ("yes" if row["in_band"] else "no")))
    add("")
    if uw.get("reason"):
        add("- %s" % uw["reason"])
    add("")
    return "\n".join(out)


# -------------------------------------------------------------------------------- I/O --
def discover_receipts(cells_root, cell_prefix: str) -> tuple:
    """(paths sorted by repeat index, skipped rows). Numeric order, never lexical."""
    root = Path(cells_root)
    paths, skipped = [], []
    if not root.is_dir():
        return [], [{"path": str(root), "reason": "cells-root is not a directory"}]
    candidates = []
    for entry in sorted(os.listdir(root)):
        if not entry.startswith(cell_prefix + "-r"):
            continue
        idx = repeat_index(entry)
        if idx is None:
            skipped.append({"path": str(root / entry),
                            "reason": "directory name has no trailing -r<k>"})
            continue
        candidates.append((idx, root / entry))
    for idx, directory in sorted(candidates, key=lambda pair: pair[0]):
        receipt = directory / "receipt.json"
        if receipt.is_file():
            paths.append(receipt)
        else:
            skipped.append({"path": str(receipt),
                            "reason": "no receipt.json (the cell has a directory but no "
                                      "scored receipt)"})
    return paths, skipped


def load_receipts(paths: list) -> tuple:
    """(receipt dicts, skipped rows) -- an unreadable file is listed, never fatal."""
    receipts, skipped = [], []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            skipped.append({"path": str(path), "reason": "unreadable: %s" % exc})
            continue
        if not isinstance(data, dict):
            skipped.append({"path": str(path), "reason": "receipt is not a JSON object"})
            continue
        receipts.append(data)
    return receipts, skipped


def infer_prefix(receipts: list, fallback: str = "(receipts)") -> str:
    for receipt in receipts:
        cell = receipt.get("cell")
        if isinstance(cell, str) and REPEAT_RE.search(cell):
            return REPEAT_RE.sub("", cell)
    return fallback


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cells-root", default=None,
                    help=r"e.g. E:\work\battlemage\sat-l1\cells (read-only)")
    ap.add_argument("--cell-prefix", default=None,
                    help="e.g. np2-p512-c2 -- repeats are <prefix>-r<k>")
    ap.add_argument("--receipt", action="append", default=None, metavar="PATH",
                    help="explicit receipt path; repeatable, overrides discovery")
    ap.add_argument("--json-out", default=None,
                    help="also write the reduction as JSON (the ONLY file this tool writes)")
    ap.add_argument("--seed", type=int, default=20260909,
                    help="bootstrap seed; the CI is deterministic for a given seed")
    ap.add_argument("--resamples", type=int, default=10000, help="bootstrap draws")
    ap.add_argument("--include-cached", action="store_true",
                    help="also reduce cached-prefix receipts (no cache_prompt:false, or "
                         "prefill_cached) -- block-1 regime; excluded by default")
    ap.add_argument("--floor-pct", type=float, default=None,
                    help="override the FF6 pp512 floor (default %.2f)" % FF6_FLOORS["pp512"])
    return ap


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.receipt:
        paths = [Path(p) for p in args.receipt]
        skipped = []
    else:
        if not args.cells_root or not args.cell_prefix:
            print("need --cells-root and --cell-prefix, or one or more --receipt paths",
                  file=sys.stderr)
            return 2
        paths, skipped = discover_receipts(args.cells_root, args.cell_prefix)

    receipts, unreadable = load_receipts(paths)
    skipped = list(skipped) + unreadable

    floors = dict(FF6_FLOORS)
    if args.floor_pct is not None:
        floors["pp512"] = args.floor_pct

    reduction = reduce_repeats(receipts, floors=floors, seed=args.seed,
                               resamples=args.resamples,
                               include_cached=bool(args.include_cached))
    prefix = args.cell_prefix or infer_prefix(receipts)

    doc = {"probe": PROBE, "schema_version": SCHEMA_VERSION, "cell_prefix": prefix,
           "receipt_paths": [str(p) for p in paths], "skipped": skipped,
           "reduction": reduction}

    print(render_markdown(doc, cell_prefix=prefix, skipped=skipped))

    if args.json_out:
        out = Path(args.json_out)
        if out.parent and str(out.parent):
            out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(doc, handle, indent=1, ensure_ascii=False)
            handle.write("\n")

    if reduction["n_included"] < 2:
        print("", file=sys.stderr)
        print("FEWER THAN 2 REPEATS INCLUDED (%d) -- a noise floor over one reading is not a "
              "floor." % reduction["n_included"], file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

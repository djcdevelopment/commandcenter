#!/usr/bin/env python3
r"""One paired sample of "what does a co-tenant cost the incumbent", appended to a JSONL series.

WHY A DEDICATED SAMPLER. Claim register #15 says a resident-but-idle neighbour costs the incumbent
nothing while an actively inferring one on the same B70 costs 8%. Nothing in the corpus measures a
NON-INFERENCE tenant -- a game, a renderer, an encoder -- against the incumbent, and on 2026-09-09
an accidental pair suggested something much more interesting than a flat tax: 55% of baseline while
the game streamed assets in, 99% one minute later with it loaded. That is a LOAD-PHASE TRANSIENT,
not a standing cost, and telling those apart needs repeats rather than a verdict.

⚠⚠ THE MISTAKE THIS EXISTS TO PREVENT, and I made it that same hour. ``ff_ratecheck`` printed
``*** FAIL: 55% of baseline. This is DEGRADATION, not noise.`` It was right about the sample and
wrong about the machine. One sample is not a regime (a standing rule in this lab, and it has now
fired on me twice in one day). So this tool refuses to summarise a steady state below
``MIN_STEADY_SAMPLES`` clean samples, and every row carries its own spread so a reader can see the
contention jitter that distinguishes a dip from a level.

WHY THE SPREAD IS THE TELL. The degraded pair read a 21.83% repeat spread; the healthy pair read
0.34%. A genuine regime change moves the mean and keeps the spread tight; contention moves both.
A row without its spread is not quotable, so ``summarise`` will not emit one.

COST TO THE MACHINE THIS IS MEASURING. Three short completions per sample, plus one passive
b70tools read. That is deliberately tiny: the operator is doing real work on this box and the
instrument must not become the load. Space samples; never sample twice inside a minute.

Reads only. Appends one row per call to the series file and prints it.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ff_census  # noqa: E402
import ff_ratecheck  # noqa: E402

#: A steady state is not claimable from fewer than this many clean samples. See the docstring.
MIN_STEADY_SAMPLES = 3

#: Above this repeat spread the sample is contention-jittered, not a stable level. Sized on the
#: two real pairs: 0.34% loaded-and-steady versus 21.83% mid-load.
JITTER_SPREAD_PCT = 5.0

#: Refuse to sample twice inside this. The instrument must not become the load.
MIN_SPACING_S = 60.0

DEFAULT_SERIES = Path(r"E:\work\battlemage\sat-l1\sustained\tenant-series.jsonl")


def card_state() -> dict:
    """Per-B70 committed GB and VRAM temperature, from the passive census."""
    try:
        census = ff_census.adapters_via_b70tools()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc), "cards": {}}
    cards = {}
    for adapter in census.get("adapters") or []:
        if "B70" not in (adapter.get("desc") or ""):
            continue
        cards[adapter.get("bdf")] = {
            "local_committed_gb": adapter.get("local_committed_gb"),
            "vram_temp_c": adapter.get("vram_temp_c"),
        }
    return {"ok": bool(cards), "cards": cards}


def tenant_processes(names=("valheim", "ComfyUI", "python")) -> list:
    """Name and pid of candidate tenants, so a row says WHAT was running, not just that it was.

    Best effort and clearly marked as such: this is provenance for the row, never a gate.
    """
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                             text=True, timeout=30).stdout
    except Exception:  # noqa: BLE001
        return []
    found = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 2:
            continue
        image = parts[0]
        for name in names:
            if name.lower() in image.lower():
                found.append({"image": image, "pid": parts[1]})
                break
    return found


def last_sample(series_path: Path) -> dict | None:
    if not series_path.is_file():
        return None
    rows = [r for r in read_series(series_path)]
    return rows[-1] if rows else None


def read_series(series_path: Path) -> list:
    rows = []
    if not Path(series_path).is_file():
        return rows
    with io.open(series_path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def resolve_rung(name: str) -> dict | None:
    """The rung's declaration from rate-baselines.json. ``measure`` takes this dict, not a name."""
    try:
        doc = json.loads(io.open(ff_ratecheck.BASELINES, encoding="utf-8").read())
    except Exception:  # noqa: BLE001
        return None
    return (doc.get("rungs") or {}).get(name)


def take_sample(rung: str = "omen-arc", reps: int = 3, note: str = "",
                measure=None) -> dict:
    """Measure the rung and the cards together. Returns the row; does not write it."""
    declaration = resolve_rung(rung)
    before = card_state()
    started = time.time()
    measured = None
    error = None
    if declaration is None:
        error = "no rung %r in %s" % (rung, ff_ratecheck.BASELINES)
    else:
        try:
            measured = (measure or ff_ratecheck.measure)(declaration, reps)
        except Exception as exc:  # noqa: BLE001
            error = "%s: %s" % (type(exc).__name__, exc)
        if measured is not None and measured.get("ok") is False:
            error = measured.get("error")
    elapsed = time.time() - started
    after = card_state()

    decode = (measured or {}).get("decode_tok_s")
    spread = (measured or {}).get("repeat_spread_pct")
    baseline = (declaration or {}).get("baseline_decode_tok_s")

    row = {
        "schema_version": 1,
        "probe": "TENANT-SAMPLE",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "rung": rung,
        "reps": reps,
        "note": note,
        "decode_tok_s": decode,
        "repeat_spread_pct": spread,
        "prefill_tok_s": (measured or {}).get("prefill_tok_s"),
        "reps_tok_s": (measured or {}).get("decode_reps"),
        "baseline_decode_tok_s": baseline,
        "frac_of_baseline": round(decode / baseline, 4) if decode and baseline else None,
        "measure_wall_s": round(elapsed, 2),
        "error": error,
        "cards_before": before.get("cards"),
        "cards_after": after.get("cards"),
        "tenants": tenant_processes(),
        # The spread is what separates a dip from a level, so it is classified on the row
        # rather than left for a reader to remember to check.
        "jittered": (spread is not None and spread > JITTER_SPREAD_PCT),
    }
    return row


def summarise(rows: list) -> dict:
    """A steady-state claim, or a refusal that says exactly what is missing."""
    clean = [r for r in rows if r.get("decode_tok_s") and not r.get("jittered")
             and not r.get("error")]
    jittered = [r for r in rows if r.get("jittered")]
    out = {
        "n_total": len(rows), "n_clean": len(clean), "n_jittered": len(jittered),
        "min_steady_samples": MIN_STEADY_SAMPLES,
        "jitter_spread_pct": JITTER_SPREAD_PCT,
    }
    if len(clean) < MIN_STEADY_SAMPLES:
        out["steady_state"] = None
        out["reason"] = ("refusing a steady-state claim from %d clean sample(s); needs %d. "
                         "One sample is not a regime." % (len(clean), MIN_STEADY_SAMPLES))
        return out
    values = [r["decode_tok_s"] for r in clean]
    fracs = [r["frac_of_baseline"] for r in clean if r.get("frac_of_baseline")]
    out["steady_state"] = {
        "decode_tok_s_mean": round(statistics.fmean(values), 2),
        "decode_tok_s_min": round(min(values), 2),
        "decode_tok_s_max": round(max(values), 2),
        "spread_pct": round((max(values) - min(values)) / min(values) * 100.0, 2),
        "frac_of_baseline_mean": round(statistics.fmean(fracs), 4) if fracs else None,
    }
    out["reason"] = "%d clean samples, each below the %.0f%% jitter line" % (
        len(clean), JITTER_SPREAD_PCT)
    if jittered:
        out["jittered_note"] = (
            "%d sample(s) excluded as contention-jittered; they are KEPT in the series and are "
            "the transient half of the story, not noise to discard" % len(jittered))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="One paired tenant-cost sample")
    ap.add_argument("--rung", default="omen-arc")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--note", default="")
    ap.add_argument("--series", default=str(DEFAULT_SERIES))
    ap.add_argument("--summarise", action="store_true", help="summarise the series, sample nothing")
    ap.add_argument("--force", action="store_true", help="ignore the minimum spacing")
    args = ap.parse_args()

    series = Path(args.series)
    if args.summarise:
        print(json.dumps(summarise(read_series(series)), indent=1))
        return 0

    previous = last_sample(series)
    if previous and not args.force:
        try:
            age = time.time() - time.mktime(time.strptime(previous["ts"][:19],
                                                          "%Y-%m-%dT%H:%M:%S"))
            if age < MIN_SPACING_S:
                print("refusing: last sample was %.0f s ago, minimum spacing is %.0f s. "
                      "The instrument must not become the load." % (age, MIN_SPACING_S),
                      file=sys.stderr)
                return 2
        except (ValueError, KeyError):
            pass

    row = take_sample(args.rung, args.reps, args.note)
    series.parent.mkdir(parents=True, exist_ok=True)
    with io.open(series, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, indent=1))
    return 0 if row.get("decode_tok_s") else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
r"""SAT-L1 reference capture: GPU-tile power per B70 under a compute control, from b70tools.

The FF6 card defined ``saturation_duty_cycle`` against a frozen "render-lane reference" that was
never captured. The BF6 lane's own calibration shows ``compute 0.0%`` during a real encode, so the
candidate reference is a COMPUTE control: a prefill burst on production, recorded by b70tools at
1 Hz, reduced to watts per card. See docs/prereg/SATURATION-SURFACE-LAP1.md, gate 5.

What this does, in order (``--live``; the default is a dry run that prints the plan):
  1. rung state BEFORE (hearth.health.rungstate; a sample older than this run is `unknown`)
  2. start b70tools ``--run --ticks N --cadence-ms 1000 --flush-every-tick`` (self-terminating)
  3. ambient window: nothing, for ``--ambient-s`` seconds
  4. control burst: ``--clients`` threads post long prompts to :8082 /completion with
     ``cache_prompt: false`` so every request is a real prefill, for ``--burst-s`` seconds
  5. wait for b70tools to exit, reduce: per card, watts = dJ/dt between consecutive ticks,
     reported separately for the AMBIENT window and the BURST window
  6. rung state AFTER; ``corpus/verdict.py`` symmetry ratio over the same events stream
  7. one receipt JSON under ``--out``; optionally one row on the FF ledger (``--ledger``)

Timebase, measured 2026-09-09 (a wall-bracketed 5-tick capture ran 4.83 s): the ``t`` field on
``ms`` rows is NANOSECONDS on the boot-relative QPC clock -- ``time.perf_counter_ns()`` shares its
origin to the microsecond -- not raw QPC ticks. Reading it as 10 MHz ticks is wrong by 100x and
once turned a 1-second snapshot into a "105-second capture". The burst is windowed against
``perf_counter_ns()`` stamps for that reason.

Power is DERIVED, not sampled: the real IGCL collector emits cumulative energy counters only.
In this b70tools build ``gpu.energy_j_counter`` (GPU tile) streams every tick; ``card.`` is
emitted ONCE per capture and so can only bracket a whole capture; ``vram.`` is absent. ``--counter``
picks the per-tick counter and FAILS LOUDLY if the stream does not carry it, listing what it does.
Never a silent fallback. Idle with the production model resident measured ~26.5-26.8 W per B70.

Auth: the bearer comes from the launcher env (``OMEN_ARC_TOKEN``); run through
``hearth\etc\with-gateway-env.cmd`` from PowerShell. The token is never logged.

Reduce an existing stream without touching the GPU:
    python campaign/ff-probes/sat_reference_capture.py --reduce <events.jsonl> --counter gpu
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import io
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
B70TOOLS = Path(r"E:\work\b70tools\build\b70tools.exe")
LEDGER = Path(r"E:\work\battlemage\ff-probes\ff-receipts.jsonl")
DEFAULT_OUT = Path(r"E:\work\battlemage\sat-l1\reference")
COUNTERS = ("gpu", "card", "vram")
NS = 1_000_000_000


# ---------------------------------------------------------------- reduction --
def _stats(watts: list[float]) -> dict:
    if not watts:
        return {"intervals": 0, "p50_w": None, "p95_w": None, "max_w": None, "mean_w": None}
    ordered = sorted(watts)
    return {"intervals": len(watts),
            "p50_w": round(statistics.median(watts), 2),
            "p95_w": round(ordered[max(0, int(0.95 * (len(ordered) - 1)))], 2),
            "max_w": round(ordered[-1], 2),
            "mean_w": round(statistics.fmean(watts), 2)}


def reduce_stream(events_path: Path, counter: str,
                  window_ns: tuple[int, int] | None = None) -> dict:
    """Per-B70 watts from consecutive ticks of ``<counter>.energy_j_counter``.

    With ``window_ns`` = (start, end) on the perf_counter_ns clock, intervals are split into
    ``ambient`` (entirely before start) and ``burst`` (entirely inside); intervals straddling an
    edge are counted in neither and reported.
    """
    name = f"{counter}.energy_j_counter"
    samples: dict[str, list[tuple[int, float]]] = collections.defaultdict(list)
    present: set[str] = set()
    desc: dict[str, str] = {}
    card_bracket: dict[str, list[tuple[int, float]]] = collections.defaultdict(list)
    with events_path.open(encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("k") == "ai":
                desc[row["a"]] = row.get("desc") or "?"
            elif row.get("k") == "ms" and str(row.get("n", "")).endswith("energy_j_counter"):
                present.add(row["n"])
                if row["n"] == name:
                    samples[row["a"]].append((int(row["t"]), float(row["v"])))
                if row["n"] == "card.energy_j_counter":
                    card_bracket[row["a"]].append((int(row["t"]), float(row["v"])))
    if name not in present:
        raise RuntimeError(f"stream carries no {name!r}; present: {sorted(present)}")
    cards: dict[str, dict] = {}
    for adapter, rows in samples.items():
        if "B70" not in desc.get(adapter, ""):
            continue  # the iGPU is not part of the reference
        rows.sort()
        all_w, ambient_w, burst_w, straddle = [], [], [], 0
        irregular = 0
        for i in range(len(rows) - 1):
            (t0, v0), (t1, v1) = rows[i], rows[i + 1]
            dt_s = (t1 - t0) / NS
            if dt_s <= 0:
                continue
            if dt_s < 0.5 or dt_s > 2.0:
                irregular += 1
            w = (v1 - v0) / dt_s
            all_w.append(w)
            if window_ns is None:
                continue
            start, end = window_ns
            if t1 <= start:
                ambient_w.append(w)
            elif t0 >= start and t1 <= end:
                burst_w.append(w)
            elif t0 < end:
                straddle += 1
        bracket = None
        if len(card_bracket.get(adapter, [])) >= 2:
            cb = sorted(card_bracket[adapter])
            span = (cb[-1][0] - cb[0][0]) / NS
            bracket = {"samples": len(cb), "span_s": round(span, 2),
                       "mean_w": round((cb[-1][1] - cb[0][1]) / span, 2) if span > 0 else None}
        cards[adapter] = {
            "desc": desc.get(adapter), "ticks": len(rows), "irregular_intervals": irregular,
            "all": _stats(all_w),
            "ambient": _stats(ambient_w) if window_ns else None,
            "burst": _stats(burst_w) if window_ns else None,
            "straddling_intervals": straddle if window_ns else None,
            "card_bracket": bracket if bracket else
            {"note": "card.energy_j_counter emitted once per capture in this build; bracket needs >=2 samples",
             "samples": len(card_bracket.get(adapter, []))},
            "watts_by_interval": [round(w, 1) for w in all_w],
        }
    return {"counter": name, "counters_present": sorted(present), "timebase": "t is ns on the perf_counter clock",
            "window_ns": list(window_ns) if window_ns else None, "cards": cards}


def symmetry(events_path: Path) -> dict | None:
    """corpus/verdict.py's sample-count symmetry across the two identical cards."""
    cmd = [sys.executable, str(REPO / "corpus" / "verdict.py"), str(events_path), "--json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    if not proc.stdout.strip():
        return {"error": f"verdict.py rc={proc.returncode}", "stderr": proc.stderr[-400:]}
    try:
        doc = json.loads(proc.stdout)
    except ValueError:
        return {"error": "verdict.py emitted non-JSON", "head": proc.stdout[:200]}
    checks = doc.get("checks") or {}
    return checks.get("symmetry") or doc.get("symmetry_check") or {"note": "no symmetry block", "keys": sorted(doc)}


# ------------------------------------------------------------------- guard --
def rung_state(since: float | None) -> dict:
    sys.path.insert(0, str(REPO))
    from hearth.health.rungstate import live_rung_state  # passive reader, no auth
    try:
        state = live_rung_state()
    except Exception as exc:  # noqa: BLE001 - recorded, never a silent pass
        return {"verdict": "unreadable", "error": str(exc), "fresh": False}
    age = state.get("observed_age_s")
    fresh = True if since is None else (isinstance(age, (int, float)) and time.time() - float(age) >= since)
    return {"verdict": state.get("verdict"), "observed_tok_s": state.get("observed_tok_s"),
            "frac_of_baseline": state.get("frac_of_baseline"), "observed_at": state.get("observed_at"),
            "fresh": bool(fresh)}


def wait_for_fresh(since: float, timeout_s: float, poll_s: float = 15.0) -> dict:
    deadline = time.time() + timeout_s
    record = rung_state(since)
    while not record.get("fresh") and time.time() < deadline:
        time.sleep(poll_s)
        record = rung_state(since)
    record["waited_s"] = round(timeout_s - max(deadline - time.time(), 0.0), 1)
    return record


# ------------------------------------------------------------- the burst --
def make_prompt(target_tokens: int) -> str:
    unit = "The saturation reference measures board power under a real prefill burst. "
    return unit * max(1, int(target_tokens * 4.2 / len(unit)))  # ~4.2 bytes/token English


def burst(port: int, token: str | None, prompt: str, clients: int, seconds: float, n_predict: int) -> dict:
    url = f"http://127.0.0.1:{port}/completion"
    stop = time.time() + seconds
    results: list[dict] = []
    lock = threading.Lock()

    def client(cid: int) -> None:
        while time.time() < stop:
            body = json.dumps({"prompt": prompt, "n_predict": n_predict, "temperature": 0,
                               "cache_prompt": False}).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            if token:
                req.add_header("Authorization", "Bearer " + token)
            t0 = time.time()
            try:
                with urllib.request.urlopen(req, timeout=900) as resp:
                    doc = json.loads(resp.read().decode("utf-8"))
                timings = doc.get("timings") or {}
                row = {"client": cid, "ok": True, "wall_s": round(time.time() - t0, 3),
                       "prompt_n": timings.get("prompt_n"), "prompt_per_second": timings.get("prompt_per_second"),
                       "predicted_per_second": timings.get("predicted_per_second")}
            except urllib.error.HTTPError as exc:
                row = {"client": cid, "ok": False, "http": exc.code, "wall_s": round(time.time() - t0, 3)}
            except Exception as exc:  # noqa: BLE001
                row = {"client": cid, "ok": False, "error": str(exc)[:200], "wall_s": round(time.time() - t0, 3)}
            with lock:
                results.append(row)

    threads = [threading.Thread(target=client, args=(i,), daemon=True) for i in range(clients)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ok = [r for r in results if r.get("ok")]
    return {"requests": len(results), "ok": len(ok), "clients": clients, "seconds": seconds,
            "prompt_n": sorted({r.get("prompt_n") for r in ok})[:4],
            "prefill_tok_s_median": round(statistics.median(r["prompt_per_second"] for r in ok), 1) if ok else None,
            "rows": results}


# --------------------------------------------------------------------- main --
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="actually capture; default is a dry run")
    ap.add_argument("--reduce", type=Path, default=None, help="reduce an existing events.jsonl and exit")
    ap.add_argument("--counter", choices=COUNTERS, default="gpu")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--port", type=int, default=8082)
    ap.add_argument("--ambient-s", type=float, default=20.0)
    ap.add_argument("--burst-s", type=float, default=90.0)
    ap.add_argument("--clients", type=int, default=2)
    ap.add_argument("--prompt-tokens", type=int, default=8192)
    ap.add_argument("--n-predict", type=int, default=16)
    ap.add_argument("--settle-timeout", type=float, default=420.0)
    ap.add_argument("--ledger", action="store_true", help="append one SAT-L1-REFERENCE row to the FF ledger")
    args = ap.parse_args()

    if args.reduce:
        print(json.dumps(reduce_stream(args.reduce, args.counter), indent=1))
        return 0

    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out = args.out / f"ref-{stamp}"
    ticks = int(args.ambient_s + args.burst_s + 30)
    plan = {"probe": "SAT-L1-REFERENCE", "stamp": stamp, "out": str(out), "counter": args.counter,
            "b70tools": [str(B70TOOLS), "--run", "--ticks", str(ticks), "--cadence-ms", "1000",
                         "--flush-every-tick", "--out", str(out / "b70")],
            "ambient_s": args.ambient_s, "burst": {"clients": args.clients, "seconds": args.burst_s,
                                                   "prompt_tokens": args.prompt_tokens, "n_predict": args.n_predict,
                                                   "endpoint": f"http://127.0.0.1:{args.port}/completion",
                                                   "cache_prompt": False},
            "timebase": "b70tools t = ns on the perf_counter clock (measured 2026-09-09)",
            "bearer": {"env": "OMEN_ARC_TOKEN", "present": bool(os.environ.get("OMEN_ARC_TOKEN")),
                       "length": len(os.environ.get("OMEN_ARC_TOKEN", ""))}}
    if not args.live:
        print("DRY RUN -- nothing captured. Plan:")
        print(json.dumps(plan, indent=1))
        return 0
    if not B70TOOLS.is_file():
        print(f"b70tools not found at {B70TOOLS}", file=sys.stderr)
        return 2
    token = os.environ.get("OMEN_ARC_TOKEN") or None

    out.mkdir(parents=True, exist_ok=False)
    (out / "b70").mkdir()
    receipt: dict = {"schema_version": 1, **plan, "started_utc": dt.datetime.now(dt.UTC).isoformat()}
    started = time.time()
    receipt["rung_before"] = rung_state(None)
    if receipt["rung_before"].get("verdict") not in ("at_rate", "warn"):
        receipt["status"] = "refused"
        receipt["reason"] = f"rung not serving before capture: {receipt['rung_before']}"
        (out / "receipt.json").write_text(json.dumps(receipt, indent=1), encoding="utf-8")
        print(json.dumps(receipt, indent=1))
        return 1

    launch_ns = time.perf_counter_ns()
    proc = subprocess.Popen(plan["b70tools"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(args.ambient_s)
    burst_start_ns = time.perf_counter_ns()
    receipt["burst"] = {**plan["burst"], **burst(args.port, token, make_prompt(args.prompt_tokens),
                                                  args.clients, args.burst_s, args.n_predict)}
    burst_end_ns = time.perf_counter_ns()
    receipt["clock"] = {"launch_ns": launch_ns, "burst_start_ns": burst_start_ns, "burst_end_ns": burst_end_ns,
                        "burst_wall_s": round((burst_end_ns - burst_start_ns) / NS, 2)}
    try:
        proc.wait(timeout=ticks + 60)
    except subprocess.TimeoutExpired:
        proc.kill()
        receipt["b70tools_killed"] = True
    receipt["b70tools_exit"] = proc.returncode
    events = out / "b70" / "events.jsonl"
    try:
        receipt["power"] = reduce_stream(events, args.counter, (burst_start_ns, burst_end_ns))
        first_tick = min(int(json.loads(l)["t"]) for l in events.open(encoding="utf-8-sig")
                         if '"k":"ms"' in l and "energy_j_counter" in l)
        receipt["clock"]["first_tick_after_launch_s"] = round((first_tick - launch_ns) / NS, 3)
    except Exception as exc:  # noqa: BLE001 - the loud failure the docstring promises
        receipt["power"] = {"error": str(exc)}
    receipt["symmetry"] = symmetry(events)
    # A keep-alive deep probe that fires INSIDE the burst measures the burst's queueing, not
    # recovery (2026-09-09: 3.56 tok/s at 28 s into a 94 s burst). Keep it as `rung_during`, and
    # only a sample taken after the burst ENDED counts as "after".
    burst_end_wall = started + (burst_end_ns - launch_ns) / NS
    receipt["rung_during"] = rung_state(started)
    receipt["rung_after"] = wait_for_fresh(burst_end_wall, args.settle_timeout)
    receipt["production_unaffected"] = bool(receipt["rung_after"].get("fresh")
                                            and receipt["rung_after"].get("verdict") in ("at_rate", "warn"))
    receipt["status"] = "executed" if "error" not in receipt["power"] else "fail"
    receipt["finished_utc"] = dt.datetime.now(dt.UTC).isoformat()
    (out / "receipt.json").write_text(json.dumps(receipt, indent=1), encoding="utf-8")

    summary = {k: receipt[k] for k in ("status", "rung_before", "rung_after", "production_unaffected", "clock")}
    summary["burst"] = {k: v for k, v in receipt["burst"].items() if k != "rows"}
    if "error" not in receipt["power"]:
        summary["power"] = {"counter": receipt["power"]["counter"],
                            "cards": {a: {"desc": c["desc"], "ticks": c["ticks"], "irregular": c["irregular_intervals"],
                                          "ambient": c["ambient"], "burst": c["burst"],
                                          "straddling": c["straddling_intervals"], "card_bracket": c["card_bracket"]}
                                      for a, c in receipt["power"]["cards"].items()}}
    else:
        summary["power"] = receipt["power"]
    summary["symmetry"] = receipt["symmetry"]
    print(json.dumps(summary, indent=1))
    if args.ledger and receipt["status"] == "executed":
        row = {"ts": receipt["finished_utc"], "probe": "SAT-L1-REFERENCE", "cell": f"control-{stamp}",
               "coresident": True, "counter": receipt["power"]["counter"],
               "burst_p50_w": {a: (c["burst"] or {}).get("p50_w") for a, c in receipt["power"]["cards"].items()},
               "ambient_p50_w": {a: (c["ambient"] or {}).get("p50_w") for a, c in receipt["power"]["cards"].items()},
               "receipt": str(out / "receipt.json")}
        with io.open(LEDGER, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return 0 if receipt["status"] == "executed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

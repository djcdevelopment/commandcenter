#!/usr/bin/env python3
"""B4 -- does Flash's -42% co-residency tax survive controlled warmth?

THE CLAIM UNDER TEST. The four-venue lap recorded Flash falling 5.04 -> 2.93 tok/s "once
both seats went live" -- a -42% co-residency tax on the strategic tier. It was measured
without a rate gate on the incumbent and before ADR-0043, so it may be genuine contention,
cold-state contamination, or a mixture.

*** A CONFIG MISMATCH THAT HAS TO BE SAID OUT LOUD. *** The -42% was measured on Flash at
`-ngl 0` (weights on host, ~1 GB Vulkan compute buffer doing the work). The clean solo
baseline from W-A -- 14.56 tok/s -- is a DIFFERENT config: `-ot .ffn_.*_exps.=CPU` with
attention and KV resident on both B70s (2.1 / 2.4 GB). Scoring the -42% claim against
14.56 would compare two venues and call the difference a co-residency tax. So this probe
brackets BOTH configs: `ngl0` answers the historical claim on its own terms, and `expcpu`
is the config the venue matrix actually uses and the one with a trustworthy solo number.

LOCAL BRACKET, per config: SOLO -> CO-RESIDENT -> SOLO. Flash gets its own within-session
drift control instead of leaning on a historical baseline measured on another day, on
another epoch, by another rig. If the two solo arms disagree, the co-resident number in
between is not interpretable.

INCUMBENT HEALTH IS A GATE, NOT A RESULT. For every co-resident cell:
  1. production restarted and demonstrably warm;
  2. deep decode-capable ff_ratecheck immediately before  -> pre-rate;
  3. placement asserted from per-BDF residency, inside the window the pre-rate created;
  4. the Flash measurement;
  5. production measured DURING, concurrently with Flash inference;
  6. deep ff_ratecheck immediately after Flash exits            -> post-rate;
  7. the Flash number is accepted ONLY if the incumbent was inside its valid-rate envelope
     both BEFORE and AFTER (see the next paragraph on why not during).

*** INCUMBENT DEGRADATION IS NEVER PART OF THE FLASH TAX. *** If production ends the cell
outside its envelope the epoch is marked INVALID. The only conclusion such an epoch
supports is that the interaction CAN induce incumbent degradation; it cannot establish a
steady-state Flash tax, and the degraded period is not folded into the number.

*** WHICH RATES GATE, AND WHY IT IS NOT ALL THREE. *** PRE and POST gate; DURING does not.
A co-tenant slowing the incumbent WHILE THEY SHARE is the phenomenon, not contamination --
gating on it would make every co-residency cell invalid by construction and the probe could
never return a number. What must be excluded is the machine being bad going IN (pre) or
staying bad AFTER (post, the ADR-0043 signature). The during-rate is therefore recorded as
a RESULT in its own right: it is the reciprocal tax, what Flash costs production, which is
a separate and independently interesting quantity. A third state, VALID_INCOMPLETE_RECOVERY,
marks a cell whose post-rate is inside the absolute envelope but materially below its own
pre-rate -- some ADR-0043 contamination is present and the Flash number is provisional.

Every quantity is kept as its own observation -- Flash solo, Flash co-resident, Flash local
drift, incumbent pre/during/post, placement evidence, warm-state validity -- because
collapsing them is exactly how the original -42% became uninterpretable.

Interpretation, fixed before the run (R1: an explanation is not promoted merely because
the original one failed):
  * near -42% with production healthy      -> B4 SUPPORTED
  * penalty survives but materially smaller -> replace the figure with the controlled
                                               estimate; attribute the gap to mixed
                                               co-residency / cold-state contamination
  * penalty largely gone                    -> the historical causal claim is REFUTED
  * production degrades                     -> INVALID epoch; restart, do not interpret
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ff_cell        # noqa: E402
import ff_census      # noqa: E402
import ff_ratecheck   # noqa: E402

LEDGER = r"E:\work\battlemage\ff-probes\ff-receipts.jsonl"
SENTINEL = r"C:\work\commandcenter\hearth\var\arc-maintenance.stop"
QWEN38 = r"E:\work\llamacpp-qwen38\build\bin\llama-server.exe"
FLASH = (r"E:\work\battlemage\models\qwen38-flash-next\UD-IQ4_XS"
         r"\Qwen3.8-Flash-Next-UD-IQ4_XS-00001-of-00003.gguf")
LOGDIR = r"E:\work\battlemage\ff-probes\b4-flash"
PORT = 18196
READY = "model loaded"
BUF_RE = re.compile(r"(\S+)\s+model buffer size\s*=\s*([\d.]+)\s*MiB")

# Both Flash venues. `ngl0` is the config the -42% was measured on; `expcpu` is the config
# with the clean W-A solo baseline (14.40 / 14.71 -> 14.56).
CONFIGS = {
    "ngl0": {
        "args": ["-ngl", "0", "-fa", "on", "-c", "8192", "-np", "1"],
        "expect_gpu_model_buffers": 0,
        "historical_solo": 5.04, "historical_tax_pct": -42.0,
        "note": "weights on host; llama.cpp still offloads compute to a B70 via a ~1 GB buffer",
    },
    "expcpu": {
        "args": ["-ngl", "99", "-sm", "layer", "-ts", "1,1", "-fa", "on", "-fit", "off",
                 "-ot", ".ffn_.*_exps.=CPU", "--no-repack", "-c", "16384", "-np", "1"],
        "expect_gpu_model_buffers": 2,
        "historical_solo": 14.56, "historical_tax_pct": -42.0,
        "note": "LZ1-A argv; attention/KV on both B70s, experts on host. W-A solo baseline.",
    },
}


def now():
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def flash_post(prompt, n_predict, timeout=1800):
    payload = {"prompt": prompt, "n_predict": n_predict, "temperature": 0,
               "cache_prompt": False}
    req = urllib.request.Request("http://127.0.0.1:%d/completion" % PORT,
                                 data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return (json.load(r).get("timings") or {})


def stop_flash(proc):
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=180)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
    # Flash mmaps ~88 GB; teardown outlives any fixed sleep (the W-A lesson).
    for _ in range(90):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=2)
            time.sleep(2)
        except Exception:  # noqa: BLE001
            return


def start_flash(cfg_name, tag):
    os.makedirs(LOGDIR, exist_ok=True)
    cfg = CONFIGS[cfg_name]
    err = os.path.join(LOGDIR, "%s-%s.err.log" % (cfg_name, tag))
    argv = ([QWEN38, "-m", FLASH, "--alias", "b4-%s" % cfg_name]
            + cfg["args"] + ["-lv", "5", "--host", "127.0.0.1",
                             "--port", str(PORT), "--slots"])
    env = dict(os.environ)
    env.pop("GGML_VK_VISIBLE_DEVICES", None)      # ADR-0042: never filter by index
    fe = io.open(err, "wb")
    p = subprocess.Popen(argv, stderr=fe, stdout=subprocess.DEVNULL, env=env)
    deadline = time.time() + 900
    while time.time() < deadline:
        if p.poll() is not None:
            raise RuntimeError("flash %s/%s exited rc=%s; see %s"
                               % (cfg_name, tag, p.returncode, err))
        try:
            if READY in io.open(err, encoding="utf-8", errors="replace").read():
                break
        except OSError:
            pass
        time.sleep(3)
    else:
        stop_flash(p)
        raise RuntimeError("flash %s/%s never reported %r" % (cfg_name, tag, READY))

    text = io.open(err, encoding="utf-8", errors="replace").read()
    bufs = [(m.group(1), float(m.group(2))) for m in BUF_RE.finditer(text)]
    gpu = [(h, v) for h, v in bufs if re.match(r"^Vulkan\d+$", h) and v > 1.0]
    if len(gpu) != cfg["expect_gpu_model_buffers"]:
        stop_flash(p)
        raise RuntimeError(
            "PLACEMENT MISMATCH [flash %s/%s]: expected %d GPU model buffers, got %d :: %s"
            % (cfg_name, tag, cfg["expect_gpu_model_buffers"], len(gpu),
               ", ".join("%s=%.1fMiB" % b for b in bufs)))
    return p, {"gpu_model_buffers": ["%s=%.1fMiB" % b for b in gpu],
               "host_buffers": ["%s=%.1fMiB" % b for b in bufs
                                if not re.match(r"^Vulkan\d+$", b[0])]}


def measure_flash(reps, n_predict):
    """Flash decode. Warm-up discarded: the first eval after a load costs 12-24x (W-A)."""
    flash_post("warm the pipeline", 16)
    out = []
    for _ in range(reps):
        t = flash_post("Write a precise technical paragraph about write-ahead logging.", n_predict)
        out.append(round(t.get("predicted_per_second") or 0.0, 2))
    return out


def arc_placement():
    cen = ff_census.adapters_via_b70tools()
    arc = [(a.get("bdf"), a.get("local_committed_gb"), a.get("non_local_committed_gb"))
           for a in cen.get("adapters") or [] if "Arc" in (a.get("desc") or "")]
    vals = [x[1] for x in arc if isinstance(x[1], (int, float))]
    assertion = "both-b70" if len(vals) == 2 and min(vals) >= 10.0 else "indeterminate"
    return assertion, arc


def production(down: bool):
    if down:
        io.open(SENTINEL, "w", encoding="utf-8").write("B4 Flash solo arm; production DOWN.\n")
        subprocess.run(["schtasks", "/Run", "/TN", "ArcServeRestart"], capture_output=True)
        time.sleep(12)
        return True
    try:
        os.remove(SENTINEL)
    except OSError:
        pass
    t0 = datetime.now()
    subprocess.run(["schtasks", "/Run", "/TN", "ArcServeRestart"], capture_output=True)
    return ff_cell.wait_for_ready(since=t0)


def timers(action):
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                            "derek@192.168.12.220",
                            "sudo systemctl %s arc-keepalive.timer arc-keepalive-deep.timer" % action],
                           capture_output=True, text=True, timeout=40)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="B4: Flash co-residency tax under controlled warmth")
    ap.add_argument("--configs", nargs="*", default=["ngl0", "expcpu"])
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--n-predict", type=int, default=64)
    ap.add_argument("--gate-reps", type=int, default=4)
    ap.add_argument("--no-ledger", action="store_true")
    args = ap.parse_args()

    baselines = json.load(io.open(ff_ratecheck.BASELINES, encoding="utf-8"))
    rung = (baselines.get("rungs") or {}).get("omen-arc")
    base = rung["baseline_decode_tok_s"]
    envelope = ff_ratecheck.FAIL_FRAC * base

    print("=== B4: Flash co-residency tax, incumbent health gated ===")
    print("  incumbent valid-rate envelope: >= %.2f tok/s (%.0f%% of %.2f)"
          % (envelope, ff_ratecheck.FAIL_FRAC * 100, base))
    ka = timers("stop")
    print("  fx99 keep-alive timers stopped: %s" % ka)

    results = {c: {} for c in args.configs}
    try:
        # ---- SOLO (opening bracket): production down.
        production(down=True)
        print("\n--- production DOWN: Flash solo, opening bracket ---")
        for c in args.configs:
            p, place = start_flash(c, "solo-pre")
            try:
                reps = measure_flash(args.reps, args.n_predict)
                results[c]["solo_pre"] = reps
                results[c]["placement_solo"] = place
                print("  %-7s solo(pre)  %6.2f tok/s  %s :: %s"
                      % (c, statistics.fmean(reps), reps, ", ".join(place["gpu_model_buffers"]) or "no GPU model buffers"))
            finally:
                stop_flash(p)

        # ---- CO-RESIDENT: production up and warm, gated before/during/after.
        print("\n--- production UP: co-resident cells ---")
        for c in args.configs:
            ok = production(down=False)
            print("  incumbent restarted: ready=%s" % ok)
            # One retry on a connection-level failure. The incumbent can report its ready
            # marker a moment before it reliably accepts sockets, and a transient
            # "connection forcibly closed" there is not a health verdict -- it silently
            # cost this probe an entire arm on its first run.
            pre, pre_frac = ff_cell.rate(rung, args.gate_reps)
            if not pre.get("ok"):
                print("  %-7s pre-rate attempt 1 failed (%s) -- retrying once in 15 s"
                      % (c, pre.get("error")))
                time.sleep(15)
                pre, pre_frac = ff_cell.rate(rung, args.gate_reps)
            if not pre.get("ok"):
                results[c]["coresident_validity"] = "INVALID: pre-rate failed (%s)" % pre.get("error")
                # NEVER skip silently: a missing arm that announces nothing looks like a
                # design choice in the output rather than a failure.
                print("  %-7s SKIPPING co-resident cell -- %s" % (c, results[c]["coresident_validity"]))
                continue
            assertion, arc = arc_placement()
            print("  %-7s incumbent pre  %6.2f tok/s (%.0f%%)  placement %s :: %s"
                  % (c, pre["decode_tok_s"], (pre_frac or 0) * 100, assertion, arc))
            if pre["decode_tok_s"] < envelope:
                results[c]["coresident_validity"] = (
                    "INVALID: incumbent below envelope BEFORE the cell (%.2f < %.2f)"
                    % (pre["decode_tok_s"], envelope))
                print("    -> %s" % results[c]["coresident_validity"])
                continue

            p, place = start_flash(c, "cores")
            during, during_frac, flash_reps = None, None, None
            try:
                # Flash inference and the incumbent's during-rate run CONCURRENTLY -- a
                # during-rate taken after Flash stops working is a different measurement.
                box = {}

                def drive():
                    box["flash"] = measure_flash(args.reps, args.n_predict)

                th = threading.Thread(target=drive, daemon=True)
                th.start()
                time.sleep(5)   # let Flash get into its measured reps
                during, during_frac = ff_cell.rate(rung, args.gate_reps)
                th.join(timeout=1800)
                flash_reps = box.get("flash")
            finally:
                stop_flash(p)
                time.sleep(8)

            post, post_frac = ff_cell.rate(rung, args.gate_reps)
            post_ok = post.get("ok") and post["decode_tok_s"] >= envelope
            recovered = (post_ok and pre.get("decode_tok_s")
                         and post["decode_tok_s"] >= 0.95 * pre["decode_tok_s"])
            if not post_ok:
                validity = ("INVALID: incumbent left the envelope (post %.2f < %.2f) -- this epoch "
                            "shows the interaction CAN degrade the incumbent; it cannot establish "
                            "a steady-state Flash tax"
                            % (post.get("decode_tok_s") or 0.0, envelope))
            elif not recovered:
                validity = ("VALID_INCOMPLETE_RECOVERY: post %.2f is inside the envelope but only "
                            "%.0f%% of its own pre-rate %.2f -- some persistent degradation is "
                            "present, so the Flash number is provisional"
                            % (post["decode_tok_s"],
                               post["decode_tok_s"] / pre["decode_tok_s"] * 100,
                               pre["decode_tok_s"]))
            else:
                validity = "VALID"
            valid = post_ok
            results[c].update({
                "coresident": flash_reps,
                "placement_coresident": place,
                "incumbent_pre": pre.get("decode_tok_s"), "incumbent_pre_reps": pre.get("decode_reps"),
                "incumbent_during": during.get("decode_tok_s") if during and during.get("ok") else None,
                "incumbent_during_reps": during.get("decode_reps") if during and during.get("ok") else None,
                "incumbent_post": post.get("decode_tok_s") if post.get("ok") else None,
                "incumbent_post_reps": post.get("decode_reps") if post.get("ok") else None,
                "incumbent_frac_pre": round(pre_frac, 3) if pre_frac else None,
                "incumbent_frac_during": round(during_frac, 3) if during_frac else None,
                "incumbent_frac_post": round(post_frac, 3) if post_frac else None,
                "coresident_validity": validity,
                # The reciprocal tax: what Flash costs PRODUCTION while they share. A result,
                # not a gate.
                "reciprocal_tax_on_incumbent_pct": (
                    round((during["decode_tok_s"] - pre["decode_tok_s"]) / pre["decode_tok_s"] * 100, 1)
                    if (during and during.get("ok") and pre.get("decode_tok_s")) else None),
            })
            print("  %-7s flash co-res %6.2f tok/s  %s" %
                  (c, statistics.fmean(flash_reps) if flash_reps else 0.0, flash_reps))
            print("           incumbent during %s  post %s  -> %s"
                  % (during.get("decode_tok_s") if during and during.get("ok") else "n/a",
                     post.get("decode_tok_s") if post.get("ok") else "n/a",
                     results[c]["coresident_validity"]))

        # ---- SOLO (closing bracket): production down again.
        production(down=True)
        print("\n--- production DOWN: Flash solo, closing bracket ---")
        for c in args.configs:
            p, _place = start_flash(c, "solo-post")
            try:
                reps = measure_flash(args.reps, args.n_predict)
                results[c]["solo_post"] = reps
                print("  %-7s solo(post) %6.2f tok/s  %s" % (c, statistics.fmean(reps), reps))
            finally:
                stop_flash(p)
    finally:
        ok = production(down=False)
        print("\n  production restored: ready=%s" % ok)
        if ka:
            print("  fx99 keep-alive timers restarted: %s" % timers("start"))

    # ---- verdict, per config
    print("\n=== RESULT ===")
    verdicts = {}
    for c in args.configs:
        r = results[c]
        sp, sq, co = r.get("solo_pre"), r.get("solo_post"), r.get("coresident")
        if not (sp and sq and co):
            verdicts[c] = ("INCONCLUSIVE", "a bracket arm is missing")
            print("  %-7s INCONCLUSIVE -- missing arm" % c)
            continue
        m_pre, m_post, m_co = statistics.fmean(sp), statistics.fmean(sq), statistics.fmean(co)
        solo = (m_pre + m_post) / 2
        drift = abs(m_post - m_pre) / solo * 100
        tax = (m_co - solo) / solo * 100
        r.update({"solo_mean": round(solo, 2), "solo_local_drift_pct": round(drift, 2),
                  "coresident_mean": round(m_co, 2), "coresidency_tax_pct": round(tax, 1)})
        print("  %-7s solo %.2f (pre %.2f / post %.2f, local drift %.2f%%)  co-resident %.2f  "
              "tax %+.1f%%  [%s]" % (c, solo, m_pre, m_post, drift, m_co, tax,
                                     r.get("coresident_validity", "?")))
        validity = r.get("coresident_validity", "?")
        hist = CONFIGS[c].get("historical_tax_pct", -42.0)
        # TWO SEPARATE QUESTIONS, and conflating them is a real error: "can I resolve the
        # true tax from zero?" and "can I exclude the historical -42%?" A run can easily
        # answer the second and not the first. Refusing to conclude anything because the
        # residual effect is inside the drift floor would throw away a decisive refutation.
        excluded = abs(tax - hist) > 3 * max(drift, 0.5)
        resolvable = abs(tax) > 2 * max(drift, 0.5)
        r["historical_tax_excluded"] = bool(excluded)
        r["effect_resolvable_from_zero"] = bool(resolvable)
        if validity.startswith("INVALID"):
            v = ("INVALID EPOCH",
                 "the incumbent left its envelope, so this shows the interaction CAN degrade it "
                 "but cannot establish a steady-state Flash tax")
        elif not excluded:
            v = ("INCONCLUSIVE",
                 "tax %+.1f%% against %.2f%% drift cannot be distinguished from the historical "
                 "%+.0f%%" % (tax, drift, hist))
        elif tax <= -60.0:
            v = ("WORSE THAN HISTORICAL",
                 "tax %+.1f%% is far beyond the recorded %+.0f%%; the historical figure UNDERSTATED "
                 "the cost on this config" % (tax, hist))
        elif tax <= -30.0:
            v = ("SUPPORTED", "tax %+.1f%% is near the historical %+.0f%% with the incumbent healthy"
                 % (tax, hist))
        elif tax <= -8.0:
            v = ("REDUCED", "tax %+.1f%% survives but is materially smaller than %+.0f%%; the gap is "
                            "attributable to cold-state contamination in the original" % (tax, hist))
        elif resolvable:
            v = ("REFUTED", "tax %+.1f%% -- a real but small cost, and the historical %+.0f%% is "
                            "excluded by %.0fx the drift floor" % (tax, hist, abs(tax - hist) / max(drift, 0.5)))
        else:
            v = ("REFUTED (magnitude unresolved)",
                 "the historical %+.0f%% is excluded, but the residual tax %+.1f%% is inside the "
                 "%.2f%% drift floor and cannot be distinguished from zero -- the honest statement "
                 "is 'at most a few percent', not a point estimate" % (hist, tax, drift))
        if validity.startswith("VALID_INCOMPLETE"):
            v = (v[0] + " (PROVISIONAL)", v[1] + " -- but the incumbent did not fully recover, so "
                                                 "some ADR-0043 contamination remains in this cell")
        verdicts[c] = v
        print("           VERDICT %s -- %s" % v)

    row = {"ts": now(), "probe": "B4-FLASH-CORESIDENCY", "cell": "flash-tax-bracketed",
           "configs": args.configs, "results": results,
           "verdicts": {k: {"verdict": v[0], "reason": v[1]} for k, v in verdicts.items()},
           "incumbent_envelope_tok_s": round(envelope, 2),
           "keepalive_timers_stopped": ka,
           "config_assertion": "load_report",
           "historical_claim": "-42% (5.04 -> 2.93 tok/s), measured on the ngl0 config without a "
                               "rate gate on the incumbent and before ADR-0043",
           "config_mismatch_note": "the W-A solo baseline of 14.56 is the expcpu config, NOT the "
                                   "ngl0 config the -42% was measured on; scoring one against the "
                                   "other would compare two venues and call it a co-residency tax",
           "receipt_status": "CONTROLLED_REMEASUREMENT",
           "receipt_status_reason": "solo->coresident->solo bracket per config; incumbent health "
                                    "gated pre/during/post; degraded epochs invalidated, never "
                                    "folded into the tax"}
    if not args.no_ledger:
        with io.open(LEDGER, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

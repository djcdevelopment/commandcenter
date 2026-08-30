#!/usr/bin/env python3
"""B3 -- dual-split vs single-card: find the CROSSOVER, not a yes/no cell.

The live unresolved question. W-A settled 512 tokens on a serving topology for the first
time (dual-split costs 5.5% decode and buys -0.6%, i.e. nothing, on 512-token prefill),
but the recorded "+27% / +42% prefill" for dual-split was measured at pp2048 on
llama-bench -- which has no `-np` (finding A5) and so cannot express a serving topology at
all. So the crossover is real and its LOCATION is unmeasured.

A single pp2048 cell would be the wrong shape of answer. If 2048 comes back near zero we
would not know whether the crossover sits just past it or far beyond, so this samples
512 (the reproduced anchor), 2048 (the target), and 8192 (the discriminator): if 2048 is
near zero, 8192 says whether the crossover was found or merely approached.

PROTOCOL, preserved deliberately from the -ub A/B so the crossover point is directly
comparable to it and to FF6c:
  * arms INTERLEAVED dual-single-dual-single (R3: A-then-B cannot separate "B is faster"
    from "the machine got faster");
  * WITHIN-CONFIG DRIFT is the noise floor -- repeats of the same topology must agree, or
    no between-topology claim is supportable (R3);
  * WARM-ONLY: every arm loads, then warms, then is measured (R6);
  * IDENTICAL LOAD on both arms for the spill figure, because one arm's number alone
    proves nothing (R4);
  * PLACEMENT ASSERTED from the server's own load report, counting GPU buffers only --
    host buffers are not devices (ADR-0042).

WHY THIS RUNS AT -c 32768 AND NOT PRODUCTION CONTEXT. A single-card arm cannot hold
`-c 131072`: KV is ~12 GB there, so model + KV + compute is ~30.1 GB of a 32.5 GB card,
which is exactly the ADR-0042 defect footprint. Nor can either arm run beside production,
which already holds ~15 GB on each card while a single-card arm needs ~19 GB on one. So
production comes DOWN and the comparison is made at a probe context. ⚠ That is a real
scope limit and it is recorded on every row: this measures the TOPOLOGY question, not
production's operating point.

`-np 2` throughout, because the topology question is about a serving configuration and
the whole lesson of A5 is that one slot is not the regime production runs in.

⚠ WITH -np N THE CONTEXT IS SPLIT: each slot gets c/N tokens, not c. At -c 16384 -np 2 a
slot holds 8192, so an 8192-token prompt exactly exhausts it and the server answers HTTP
400 -- mid-run, with production already down. Hence -c 32768 (16384 per slot) and the
pre-flight check in main(). Production's own -c 131072 -np 2 gives each slot 65536.
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
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ff_census      # noqa: E402
import ff_ratecheck   # noqa: E402

LEDGER = r"E:\work\battlemage\ff-probes\ff-receipts.jsonl"
SENTINEL = r"C:\work\commandcenter\hearth\var\arc-maintenance.stop"
BIN = r"E:\work\llamacpp-knee\build\bin\llama-server.exe"
MODEL = r"E:\work\battlemage\models\Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf"
LOGDIR = r"E:\work\battlemage\ff-probes\b3-topology"
CORPUS = r"C:\work\commandcenter\campaign\lz-probes\kit\probe-prompt.txt"
PORT = 18195
READY = "model loaded"
BUF_RE = re.compile(r"(\S+)\s+model buffer size\s*=\s*([\d.]+)\s*MiB")

TOPOLOGIES = {
    # No visibility filter anywhere: device selection is by TYPE (ADR-0042). The topology
    # is said with the tensor split, which is the only order-independent way to say it.
    "dual":   {"ts": "1,1", "expect_gpu_buffers": 2},
    "single": {"ts": "1,0", "expect_gpu_buffers": 1},
}


def now():
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def prompt_of(approx_tokens, tag):
    corpus = io.open(CORPUS, encoding="utf-8", errors="replace").read()
    prefix = "[b3 %s %s] " % (tag, os.urandom(6).hex())
    return prefix + corpus[:max(0, approx_tokens * 4 - len(prefix))]


def post(prompt, n_predict, slot=None, timeout=900):
    payload = {"prompt": prompt, "n_predict": n_predict, "temperature": 0,
               "cache_prompt": False}
    if slot is not None:
        payload["id_slot"] = slot
    req = urllib.request.Request("http://127.0.0.1:%d/completion" % PORT,
                                 data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.load(r)
    return body.get("timings") or {}, (time.time() - t0) * 1000.0


def stop_probe(proc):
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=120)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
    # Wait for the port to actually free; a fixed sleep is what voided a W-A cell.
    for _ in range(60):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=2)
            time.sleep(2)
        except Exception:  # noqa: BLE001
            return


def start_probe(topo, ctx, nparallel, rep, model=None, tag="b3", logdir=None):
    """Launch one probe server. `model`/`tag`/`logdir` let B5 reuse this unchanged --
    the launch, ready-gate and placement assert are the parts worth not rewriting."""
    model = model or MODEL
    logdir = logdir or LOGDIR
    os.makedirs(logdir, exist_ok=True)
    err = os.path.join(logdir, "%s-%s-rep%d.err.log" % (tag, topo, rep))
    cfg = TOPOLOGIES[topo]
    argv = [BIN, "-m", model, "--alias", "%s-%s" % (tag, topo),
            "-ngl", "99", "-sm", "layer", "-ts", cfg["ts"],
            "-fa", "on", "-fit", "off", "--no-repack",
            "-c", str(ctx), "-np", str(nparallel),
            # -lv 5 is mandatory: at the default verbosity there are NO placement lines,
            # and their absence is indistinguishable from a healthy load (ADR-0042).
            "-lv", "5",
            "--host", "127.0.0.1", "--port", str(PORT), "--slots"]
    env = dict(os.environ)
    env.pop("GGML_VK_VISIBLE_DEVICES", None)
    fe = io.open(err, "wb")
    p = subprocess.Popen(argv, stderr=fe, stdout=subprocess.DEVNULL, env=env)
    deadline = time.time() + 420
    while time.time() < deadline:
        if p.poll() is not None:
            raise RuntimeError("%s/%s rep%d exited rc=%s; see %s" % (tag, topo, rep, p.returncode, err))
        try:
            if READY in io.open(err, encoding="utf-8", errors="replace").read():
                break
        except OSError:
            pass
        time.sleep(3)
    else:
        stop_probe(p)
        raise RuntimeError("%s/%s rep%d never reported %r" % (tag, topo, rep, READY))

    text = io.open(err, encoding="utf-8", errors="replace").read()
    bufs = [(m.group(1), float(m.group(2))) for m in BUF_RE.finditer(text)]
    gpu = [(h, v) for h, v in bufs if re.match(r"^Vulkan\d+$", h) and v > 1.0]
    if len(gpu) != cfg["expect_gpu_buffers"]:
        stop_probe(p)
        raise RuntimeError(
            "PLACEMENT MISMATCH [%s/%s rep%d]: expected %d GPU buffers, got %d :: %s. "
            "Per ADR-0042 this is what a reshuffled enumeration looks like; the run is void."
            % (tag, topo, rep, cfg["expect_gpu_buffers"], len(gpu),
               ", ".join("%s=%.1fMiB" % b for b in bufs)))
    return p, {"gpu_buffers": ["%s=%.1fMiB" % b for b in gpu],
               "host_buffers": ["%s=%.1fMiB" % b for b in bufs
                                if not re.match(r"^Vulkan\d+$", b[0])]}


def measure(topo, rep, ctx, nparallel, lengths, dec_reps, conc_reps):
    print("\n=== arm: %s (rep %d) ===" % (topo, rep))
    proc, place = start_probe(topo, ctx, nparallel, rep)
    try:
        print("  placement OK :: %s" % ", ".join(place["gpu_buffers"]))
        for _ in range(3):          # warm-up, discarded (R6)
            post("warm the pipeline", 32)

        pre = {}
        for size in lengths:
            rates = []
            for _ in range(2):
                t, _w = post(prompt_of(size, "%s%d" % (topo, size)), 8)
                rates.append(round(t.get("prompt_per_second") or 0.0, 2))
            pre[size] = rates
            print("  prefill@%-5s %8.2f tok/s  %s" % (size, statistics.fmean(rates), rates))

        dec = []
        for _ in range(dec_reps):
            t, _w = post(ff_ratecheck.PROMPT, 100)
            dec.append(round(t.get("predicted_per_second") or 0.0, 2))
        print("  decode      %8.2f tok/s  %s" % (statistics.fmean(dec), dec))

        conc = []
        for _ in range(conc_reps):
            with ThreadPoolExecutor(max_workers=2) as ex:
                t0 = time.time()
                futs = [ex.submit(post, ff_ratecheck.PROMPT + (" %d" % i), 100, i)
                        for i in (0, 1)]
                res = [f.result() for f in futs]
                wall = time.time() - t0
            toks = sum((t.get("predicted_n") or 0) for t, _ in res)
            conc.append(round(toks / wall, 2))
        print("  np%d aggregate %6.2f tok/s  %s" % (nparallel, statistics.fmean(conc), conc))

        # Spill, sampled inside the activity window the measurements just created (A12).
        cen = ff_census.adapters_via_b70tools()
        arc = [(a.get("bdf"), a.get("local_committed_gb"), a.get("non_local_committed_gb"))
               for a in cen.get("adapters") or [] if "Arc" in (a.get("desc") or "")]
        non_local = sum(x[2] or 0 for x in arc)
        print("  residency %s  non_local total %.3f GB" % (arc, non_local))
    finally:
        stop_probe(proc)

    return {"topology": topo, "rep": rep, "context": ctx, "n_parallel": nparallel,
            "placement_assertion": topo, "placement_evidence": place["gpu_buffers"],
            "host_buffers": place["host_buffers"],
            "config_assertion": "load_report",
            "prefill": pre,
            "prefill_mean": {k: round(statistics.fmean(v), 2) for k, v in pre.items()},
            "decode_reps": dec, "decode_mean": round(statistics.fmean(dec), 2),
            "concurrency_aggregate": conc,
            "concurrency_aggregate_mean": round(statistics.fmean(conc), 2),
            "residency_per_bdf": arc, "non_local_total_gb": round(non_local, 3)}


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
    ap = argparse.ArgumentParser(description="B3: dual vs single, crossover shape")
    ap.add_argument("--lengths", type=int, nargs="*", default=[512, 2048, 8192])
    ap.add_argument("--order", nargs="*", default=["dual", "single", "dual", "single"])
    ap.add_argument("--context", type=int, default=32768)  # 16384 per slot at -np 2
    ap.add_argument("--np", dest="nparallel", type=int, default=2)
    ap.add_argument("--dec-reps", type=int, default=5)
    ap.add_argument("--conc-reps", type=int, default=3)
    ap.add_argument("--no-ledger", action="store_true")
    args = ap.parse_args()

    print("=== B3: dual-split vs single-card, crossover shape ===")
    print("  context %d, -np %d, lengths %s, order %s"
          % (args.context, args.nparallel, args.lengths, args.order))

    # PRE-FLIGHT: with -np N the context is SPLIT, so each slot gets c/N tokens -- not c.
    # A prompt larger than one slot returns HTTP 400 mid-run, after production is already
    # down. Caught the hard way at -c 16384 -np 2, where an 8192-token prompt exactly
    # exhausted a slot and the run died on its third length. Fail here instead, before
    # anything is taken offline.
    per_slot = args.context // args.nparallel
    need = max(args.lengths) + 64          # + generation and template headroom
    if need > per_slot:
        print("  REFUSING: -c %d / -np %d = %d tokens per slot, but the largest prompt needs "
              "~%d. Raise -c to at least %d."
              % (args.context, args.nparallel, per_slot, need, need * args.nparallel))
        return 2
    print("  per-slot context %d tokens (largest prompt needs ~%d) -- OK" % (per_slot, need))

    ka = timers("stop")
    print("  fx99 keep-alive timers stopped: %s" % ka)
    io.open(SENTINEL, "w", encoding="utf-8").write(
        "B3 topology crossover. Production DOWN deliberately; neither arm fits beside it.\n")
    subprocess.run(["schtasks", "/Run", "/TN", "ArcServeRestart"], capture_output=True)
    time.sleep(10)
    print("  production stopped for the window")

    arms = []
    try:
        counts = {}
        for topo in args.order:
            counts[topo] = counts.get(topo, 0) + 1
            arms.append(measure(topo, counts[topo], args.context, args.nparallel,
                                args.lengths, args.dec_reps, args.conc_reps))
    finally:
        try:
            os.remove(SENTINEL)
        except OSError:
            pass
        t0 = datetime.now()
        subprocess.run(["schtasks", "/Run", "/TN", "ArcServeRestart"], capture_output=True)
        import ff_cell
        print("\n  production restored: ready=%s" % ff_cell.wait_for_ready(since=t0))
        if ka:
            print("  fx99 keep-alive timers restarted: %s" % timers("start"))

    # --- verdict: the crossover shape
    by = {}
    for a in arms:
        by.setdefault(a["topology"], []).append(a)
    print("\n=== RESULT ===")
    summary = {}
    for topo in ("dual", "single"):
        if topo not in by:
            continue
        p = {k: round(statistics.fmean([a["prefill_mean"][k] for a in by[topo]]), 2)
             for k in by[topo][0]["prefill_mean"]}
        d = [a["decode_mean"] for a in by[topo]]
        c = [a["concurrency_aggregate_mean"] for a in by[topo]]
        nl = [a["non_local_total_gb"] for a in by[topo]]
        summary[topo] = {"prefill": p, "decode_arms": d,
                         "decode": round(statistics.fmean(d), 2),
                         "conc_arms": c, "concurrency": round(statistics.fmean(c), 2),
                         "non_local_gb": round(statistics.fmean(nl), 3)}
        print("  %-7s prefill %s  decode %.2f %s  np%d %.2f %s  non_local %.3f GB"
              % (topo, p, summary[topo]["decode"], d, args.nparallel,
                 summary[topo]["concurrency"], c, summary[topo]["non_local_gb"]))

    verdict, reason, deltas = "INCONCLUSIVE", "need both topologies", {}
    if "dual" in summary and "single" in summary:
        s_, d_ = summary["single"], summary["dual"]
        drift = max(
            (max(v["decode_arms"]) - min(v["decode_arms"])) / statistics.fmean(v["decode_arms"])
            for v in (s_, d_)) * 100
        deltas = {k: round((d_["prefill"][k] - s_["prefill"][k]) / s_["prefill"][k] * 100, 1)
                  for k in s_["prefill"]}
        dec_delta = (d_["decode"] - s_["decode"]) / s_["decode"] * 100
        con_delta = (d_["concurrency"] - s_["concurrency"]) / s_["concurrency"] * 100
        print("\n  within-config drift (worst): %.2f%%" % drift)
        print("  DUAL vs SINGLE -- prefill by length %s" % deltas)
        print("  decode %+.1f%%   np%d aggregate %+.1f%%" % (dec_delta, args.nparallel, con_delta))

        ordered = sorted(deltas)
        signs = [deltas[k] > 0 for k in ordered]
        if drift > 3.0:
            verdict = "INCONCLUSIVE"
            reason = ("within-config drift %.2f%% exceeds the effect being measured" % drift)
        elif len(set(signs)) > 1:
            lo = next(ordered[i] for i in range(len(ordered) - 1) if signs[i] != signs[i + 1])
            hi = ordered[ordered.index(lo) + 1]
            verdict = "CROSSOVER FOUND"
            reason = ("dual-split changes sign between %d and %d tokens (%s%% -> %s%%); "
                      "decode cost %+.1f%%" % (lo, hi, deltas[lo], deltas[hi], dec_delta))
        elif all(signs):
            verdict = "DUAL WINS AT EVERY LENGTH TESTED"
            reason = ("no sign change down to %d tokens; the crossover is BELOW the range "
                      "sampled" % ordered[0])
        else:
            verdict = "DUAL LOSES AT EVERY LENGTH TESTED"
            reason = ("no sign change up to %d tokens -- the crossover was APPROACHED, not "
                      "found, and lies beyond it" % ordered[-1])
        print("\n  VERDICT: %s -- %s" % (verdict, reason))

    row = {"ts": now(), "probe": "B3-TOPOLOGY-CROSSOVER", "cell": "dual-vs-single",
           "order": args.order, "context": args.context, "n_parallel": args.nparallel,
           "arms": arms, "summary": summary, "prefill_delta_pct_dual_vs_single": deltas,
           "verdict": verdict, "verdict_reason": reason,
           "coresident": False, "keepalive_timers_stopped": ka,
           "scope_limit": ("measured at -c %d, NOT production's -c 131072: a single-card arm "
                           "cannot hold that context (KV ~12 GB puts model+KV+compute at ~30.1 GB "
                           "of a 32.5 GB card, the ADR-0042 defect footprint), and neither arm "
                           "fits beside production. This answers the TOPOLOGY question, not "
                           "production's operating point." % args.context),
           "receipt_status": "TOPOLOGY_CROSSOVER",
           "receipt_status_reason": "interleaved arms, warm-only, placement asserted from the load "
                                    "report, identical load on both arms for the spill figure"}
    if not args.no_ledger:
        with io.open(LEDGER, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

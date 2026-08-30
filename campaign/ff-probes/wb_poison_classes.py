#!/usr/bin/env python3
"""W-B / P-C -- discriminate the co-residency poisoning mechanism.

ADR-0041 established the BEHAVIOUR: a co-tenant drives the incumbent to ~0.27x, the loss
survives the co-tenant's exit and an idle period, and only a restart clears it. It did
not establish the CAUSE. This sweep measures the incumbent BEFORE / DURING / AFTER five
co-tenant classes chosen so that the answer narrows regardless of which way each goes.

  1 control              nothing runs. The drift floor -- if this moves, nothing else here
                         means anything.
  2 vulkan-load-noinfer  a Vulkan server that LOADS a model onto a B70 and never receives
                         a single request.
  3 vulkan-load-infer    the SAME server config, same model, same card -- but it serves.

  Classes 2 and 3 differ in exactly one bit: whether the co-tenant executes. That is the
  discriminator, and it is why both use the 30B on one B70 rather than reproducing the
  original Flash cell -- swapping the model as well would confound allocation-vs-execution
  with a model change. Flash's role was to demonstrate the effect; it has done that.

  4 cpu-only             `--device none -ngl 0`: a co-tenant that never touches a GPU at
                         all. If THIS poisons, the mechanism is host/WDDM-level and no
                         amount of GPU partitioning will fix it. The key cell.
  5 igpu-only            a co-tenant on a different adapter entirely. Poisoning a B70
                         incumbent from the iGPU is the second host-level signature.

Signatures: `during v / after clean` = ordinary contention. `during v / after v` =
persistent poisoning. Five cells DISCRIMINATE; they do not prove a mechanism. The output
is which experiment runs next.

Already banked, deliberately not repeated: b70tools and `llama-bench --list-devices`
(Vulkan enumeration, no model) left the incumbent at 105.08 -> 105.23 -> 105.48. Mere
Vulkan instance creation is not sufficient to poison.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ff_census      # noqa: E402
import ff_ratecheck   # noqa: E402
import ff_cell        # noqa: E402

LEDGER = r"E:\work\battlemage\ff-probes\ff-receipts.jsonl"
LOGDIR = r"E:\work\battlemage\ff-probes\wb-poison"
QWEN38 = r"E:\work\llamacpp-qwen38\build\bin\llama-server.exe"
MODEL30 = r"E:\work\battlemage\models\Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf"
COTENANT_PORT = 18192
READY_MARKER = "model loaded"

BUF_RE = re.compile(r"(\S+)\s+model buffer size\s*=\s*([\d.]+)\s*MiB")
DEV_RE = re.compile(r"using device\s+(Vulkan\d+)\s*\((.*)\)")


def now():
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def append(row):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with io.open(LEDGER, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def igpu_index():
    enum = ff_census.vulkan_enumeration()
    for d in enum.get("devices") or []:
        if "Arc" not in d["name"]:
            return d["index"], enum
    return None, enum


# --- co-tenant classes ------------------------------------------------------
# `-lv 5` on every launch: at the default verbosity there are no placement lines to
# record, and their absence is indistinguishable from a healthy load (ADR-0042).
def cotenant_argv(kind, log_name):
    """One model, one tensor-override shape, ONE variable: which device it touches.

    Every class runs Qwen3-30B-A3B with `-ot .ffn_.*_exps.=CPU`. Two reasons, both
    load-bearing:

    * FOOTPRINT. Production is dual-split at ~15 GB per 32.5 GB card. A full-weight
      co-tenant would need 17.5 GB on card 1 -- 32.5 of 32.5 -- so it would spill to host
      or fail to allocate, and the cell would measure an OOM rather than co-residency.
      With the override the GPU buffer is ~0.78 GB (measured, W-A cell S3), which is also
      the right analogue: the co-tenant that produced the original ADR-0041 event was
      Flash holding roughly 1 GB of Vulkan compute buffer, not a full card.
    * COMPARABILITY. Holding the model and the override fixed means the B70, iGPU and
      CPU-only cells differ in the device and nothing else. Swapping in a second model
      would confound adapter with architecture.
    """
    base = ["-m", MODEL30, "--alias", "wb-%s" % kind, "-fa", "on", "-fit", "off",
            "--no-repack", "-c", "8192", "-np", "1", "-lv", "5",
            "-ot", ".ffn_.*_exps.=CPU",
            "--host", "127.0.0.1", "--port", str(COTENANT_PORT), "--slots"]
    env = dict(os.environ)
    env.pop("GGML_VK_VISIBLE_DEVICES", None)
    if kind in ("vulkan-load-noinfer", "vulkan-load-infer"):
        # ONE B70, said with a tensor split rather than by naming a device (ADR-0042).
        return base + ["-ngl", "99", "-sm", "layer", "-ts", "1,0"], env, "one-b70"
    if kind == "cpu-only":
        return base + ["-ngl", "0", "--device", "none"], env, "cpu-only"
    if kind == "igpu-only":
        # The documented exception to "never filter by index": device-TYPE selection
        # deliberately drops integrated GPUs, so this venue is unreachable without one.
        # Discovered at run time, recorded on the receipt, and the placement that results
        # is read back from the load report.
        idx, enum = igpu_index()
        if idx is None:
            raise RuntimeError("no iGPU in this enumeration: %s" % enum)
        env["GGML_VK_VISIBLE_DEVICES"] = str(idx)
        return base + ["-ngl", "99", "-sm", "layer"], env, "igpu-only(filter=%d)" % idx
    raise ValueError(kind)


def start_cotenant(kind, timeout_s=420):
    os.makedirs(LOGDIR, exist_ok=True)
    argv, env, placement_intent = cotenant_argv(kind, kind)
    err = os.path.join(LOGDIR, "%s.err.log" % kind)
    out = os.path.join(LOGDIR, "%s.out.log" % kind)
    fe, fo = io.open(err, "wb"), io.open(out, "wb")
    p = subprocess.Popen([QWEN38] + argv, stderr=fe, stdout=fo, env=env)
    # Readiness is the server's own "model loaded" marker, NOT a completion and NOT
    # /health (which returns 200 mid-load).
    #
    # It has to be the marker here specifically: the obvious readiness probe -- send a
    # short completion and see if it answers -- would make the co-tenant INFER, and
    # "does it infer" is the single bit that separates class 2 from class 3. A completion
    # used as a health check would have quietly turned the control into the treatment.
    # Each co-tenant writes a fresh log, so there is no stale-marker race to guard here.
    deadline = time.time() + timeout_s
    ready = False
    while time.time() < deadline:
        if p.poll() is not None:
            raise RuntimeError("%s co-tenant exited rc=%s; see %s" % (kind, p.returncode, err))
        try:
            if READY_MARKER in io.open(err, encoding="utf-8", errors="replace").read():
                ready = True
                break
        except OSError:
            pass
        time.sleep(3)
    if not ready:
        stop_cotenant(p)
        raise RuntimeError("%s co-tenant never reported %r in %ds"
                           % (kind, READY_MARKER, timeout_s))
    text = io.open(err, encoding="utf-8", errors="replace").read()
    bufs = [(m.group(1), float(m.group(2))) for m in BUF_RE.finditer(text)]
    devs = [(m.group(1), m.group(2).strip()) for m in DEV_RE.finditer(text)]
    gpu_bufs = [(h, v) for h, v in bufs if re.match(r"^Vulkan\d+$", h) and v > 1.0]
    return p, {"intent": placement_intent,
               "buffers": ["%s=%.1fMiB" % (h, v) for h, v in bufs],
               "devices": ["%s=%s" % (h, n) for h, n in devs],
               "gpu_buffer_count": len(gpu_bufs)}


def stop_cotenant(p):
    if p is None:
        return
    try:
        p.terminate()
        p.wait(timeout=30)
    except Exception:  # noqa: BLE001
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass


class CoTenantDriver(threading.Thread):
    """Keep the co-tenant executing CONCURRENTLY with the incumbent's during-rate.

    This has to be a thread. Driving the co-tenant for the dwell and only then
    measuring the incumbent would make "during" mean *after the co-tenant stopped
    working, while it was still resident* -- which is a different question, and which
    would silently collapse the contention signature the sweep is built to detect
    (`during down / after clean` = contention). The co-tenant must still be inferring
    while the incumbent is measured, so the driver runs until it is told to stop, not
    for a fixed dwell.
    """

    def __init__(self):
        super().__init__(daemon=True)
        self._stop = threading.Event()
        self.served = 0
        self.errors = 0

    def run(self):
        while not self._stop.is_set():
            try:
                body = json.dumps({"prompt": "Explain consensus algorithms in depth.",
                                   "n_predict": 96, "temperature": 0,
                                   "cache_prompt": False}).encode()
                req = urllib.request.Request(
                    "http://127.0.0.1:%d/completion" % COTENANT_PORT, data=body,
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=300) as r:
                    json.load(r)
                self.served += 1
            except Exception:  # noqa: BLE001
                self.errors += 1
                if self.errors > 3:
                    return
                time.sleep(1)

    def stop(self):
        self._stop.set()
        self.join(timeout=330)


# --- the sweep --------------------------------------------------------------
CLASSES = ["control", "vulkan-load-noinfer", "vulkan-load-infer", "cpu-only", "igpu-only"]


def run_class(kind, rung, reps, dwell, no_ledger):
    print("\n=== W-B class: %s ===" % kind)
    print("  restarting incumbent ...")
    t0 = datetime.now()
    subprocess.run(["schtasks", "/Run", "/TN", "ArcServeRestart"], capture_output=True)
    if not ff_cell.wait_for_ready(since=t0):
        print("  FAIL -- incumbent ready marker never appeared. Class void.")
        return {"class": kind, "verdict": "INCONCLUSIVE",
                "reason": "incumbent never came back after restart"}
    epoch = ff_cell.incumbent_epoch()

    before, bfrac = ff_cell.rate(rung, reps)
    if not before.get("ok"):
        print("  FAIL -- before-rate: %s" % before.get("error"))
        return {"class": kind, "verdict": "INCONCLUSIVE", "reason": before.get("error")}
    print("  before %.2f tok/s (%.0f%% of baseline) reps=%s"
          % (before["decode_tok_s"], (bfrac or 0) * 100, before["decode_reps"]))

    # Placement of the INCUMBENT, sampled inside the window the before-rate just opened.
    cen = ff_census.adapters_via_b70tools()
    arc = [(a.get("bdf"), a.get("local_committed_gb"))
           for a in cen.get("adapters") or [] if "Arc" in (a.get("desc") or "")]
    print("  incumbent placement: %s (idle_read=%s)" % (arc, cen.get("local_committed_reads_idle")))

    if bfrac is not None and bfrac < ff_ratecheck.FAIL_FRAC:
        print("  REFUSING class -- incumbent is already below gate before the co-tenant.")
        return {"class": kind, "verdict": "INCONCLUSIVE",
                "reason": "before-rate %.2f is %.0f%% of baseline, under the %.0f%% gate"
                          % (before["decode_tok_s"], (bfrac or 0) * 100,
                             ff_ratecheck.FAIL_FRAC * 100)}

    proc, place, served, driver = None, None, 0, None
    try:
        if kind != "control":
            print("  starting co-tenant ...")
            proc, place = start_cotenant(kind)
            print("  co-tenant up: intent=%s gpu_buffers=%d :: %s"
                  % (place["intent"], place["gpu_buffer_count"], ", ".join(place["buffers"])))
        if kind == "vulkan-load-infer":
            driver = CoTenantDriver()
            driver.start()
        time.sleep(dwell)
        # Measured with the co-tenant STILL RESIDENT, and -- for class 3 -- still inferring.
        during, dfrac = ff_cell.rate(rung, reps)
        if driver is not None:
            driver.stop()
            served = driver.served
            print("  co-tenant served %d completions, concurrently, through the during-rate"
                  % served)
        if during.get("ok"):
            print("  during %.2f tok/s (%.0f%% of baseline) reps=%s"
                  % (during["decode_tok_s"], (dfrac or 0) * 100, during["decode_reps"]))
        else:
            print("  during FAILED: %s" % during.get("error"))
    finally:
        if driver is not None:
            driver.stop()
        stop_cotenant(proc)
        time.sleep(6)

    after, afrac = ff_cell.rate(rung, reps)
    if after.get("ok"):
        print("  after  %.2f tok/s (%.0f%% of baseline) reps=%s"
              % (after["decode_tok_s"], (afrac or 0) * 100, after["decode_reps"]))
    else:
        print("  after FAILED: %s" % after.get("error"))

    # Signature. The thresholds are deliberately coarse: this sweep sorts cells into
    # buckets, it does not estimate an effect size.
    DROP = 0.85  # a >15% fall against this cell's OWN before-rate counts as a drop
    d_ok, a_ok = during.get("ok"), after.get("ok")
    d_rel = (during["decode_tok_s"] / before["decode_tok_s"]) if d_ok else None
    a_rel = (after["decode_tok_s"] / before["decode_tok_s"]) if a_ok else None
    if not (d_ok and a_ok):
        verdict, reason = "INCONCLUSIVE", "a during/after measurement failed"
    elif d_rel >= DROP and a_rel >= DROP:
        verdict = "NO-EFFECT"
        reason = "neither during (%.2fx) nor after (%.2fx) fell" % (d_rel, a_rel)
    elif d_rel < DROP and a_rel >= DROP:
        verdict = "CONTENTION"
        reason = "during %.2fx recovered to %.2fx once the co-tenant left" % (d_rel, a_rel)
    elif a_rel < DROP:
        verdict = "PERSISTENT-POISONING"
        reason = "after %.2fx: the loss survived the co-tenant's exit (during %.2fx)" % (a_rel, d_rel)
    else:
        verdict, reason = "INCONCLUSIVE", "during %.2fx after %.2fx" % (d_rel, a_rel)
    print("  VERDICT %s -- %s" % (verdict, reason))

    row = {
        "ts": now(), "probe": "W-B-POISON", "cell": kind,
        "cotenant_class": kind,
        "cotenant_placement_intent": (place or {}).get("intent"),
        "cotenant_placement_evidence": (place or {}).get("buffers"),
        "cotenant_devices": (place or {}).get("devices"),
        "cotenant_completions_served": served,
        "cotenant_driven_concurrently": kind == "vulkan-load-infer",
        "dwell_s": dwell,
        "incumbent_process_epoch": epoch.get("epoch_start"),
        "incumbent_restarted_since_cotenancy": True,
        "incumbent_placement_evidence": arc,
        "before_decode_tok_s": before.get("decode_tok_s"),
        "before_reps": before.get("decode_reps"),
        "before_spread_pct": before.get("repeat_spread_pct"),
        "during_decode_tok_s": during.get("decode_tok_s"),
        "during_reps": during.get("decode_reps"),
        "after_decode_tok_s": after.get("decode_tok_s"),
        "after_reps": after.get("decode_reps"),
        "incumbent_rate_fraction_pre": round(bfrac, 3) if bfrac else None,
        "incumbent_rate_fraction_during": round(dfrac, 3) if d_ok and dfrac else None,
        "incumbent_rate_fraction_post": round(afrac, 3) if a_ok and afrac else None,
        "during_rel": round(d_rel, 3) if d_rel else None,
        "after_rel": round(a_rel, 3) if a_rel else None,
        "health_gate_passed": True,
        "verdict": verdict, "verdict_reason": reason,
        "receipt_status": "MECHANISM_DISCRIMINATION",
        "receipt_status_reason": "poisoning is the EXPECTED outcome of this cell, not a failure",
        "coresident": kind != "control",
    }
    if not no_ledger:
        append(row)
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description="W-B: discriminate the poisoning mechanism")
    ap.add_argument("--classes", nargs="*", default=CLASSES)
    ap.add_argument("--rung", default="omen-arc")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--dwell", type=int, default=45, help="seconds the co-tenant is resident")
    ap.add_argument("--no-ledger", action="store_true")
    args = ap.parse_args()

    baselines = json.load(io.open(ff_ratecheck.BASELINES, encoding="utf-8"))
    rung = (baselines.get("rungs") or {}).get(args.rung)
    if rung is None:
        print("no rung %r" % args.rung)
        return 2

    rows = []
    for kind in args.classes:
        try:
            rows.append(run_class(kind, rung, args.reps, args.dwell, args.no_ledger))
        except Exception as exc:  # noqa: BLE001
            print("  class %s raised: %s" % (kind, exc))
            rows.append({"class": kind, "verdict": "INCONCLUSIVE", "reason": str(exc)})

    print("\n=== W-B SUMMARY (class x before/during/after) ===")
    print("%-22s %9s %9s %9s  %s" % ("class", "before", "during", "after", "verdict"))
    for r in rows:
        print("%-22s %9s %9s %9s  %s"
              % (r.get("cell") or r.get("class"),
                 r.get("before_decode_tok_s", "-"), r.get("during_decode_tok_s", "-"),
                 r.get("after_decode_tok_s", "-"), r.get("verdict")))
    print("\nDiscriminators: 'vulkan-load-noinfer' vs 'vulkan-load-infer' separates "
          "allocation from execution; 'cpu-only' and 'igpu-only' separate a B70-local "
          "mechanism from a host/WDDM-level one. 'control' is the drift floor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""moe_gambit.py — stress/characterization campaign for the resident gpt-oss-120b.

Runs ON am4 against localhost:8082 (stdlib only). Every finished request is
appended to requests.jsonl immediately (crash-safe); a sampler thread records
/slots occupancy + /proc/meminfo + hwmon temps every 10s to samples.jsonl.

Sweeps:
  A  concurrency ladder: offered load 1..16 vs 4 slots (TTFT/goodput/queue-wait)
  B  context depth: 512..15k prompt tokens, over-limit failure mode, cache-hit pair
  C  decode-heavy: long generations holding slots
  F  overload burst: 32 concurrent (8x slots)
  E  sustained soak: continuous 4-way load for 600s
  D  pressure load: continuous 2-way load for 900s (memory hog stepped externally)

Usage: moe_gambit.py {smoke|A|B|C|D|E|F|all} [outdir]   (all = A,B,C,F,E)
"""
import json
import os
import random
import string
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8082"
TOKEN = os.environ.get("AM4_OXEN_TOKEN", "")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser("~/o1/gambit")
CHARS_PER_TOKEN = 4.0  # recalibrated at startup against real usage

_WORDS = ("system fleet router expert model token layer cache memory swap card "
          "compute schedule queue batch stream decode prefill matrix bench "
          "signal probe ledger door worker plan build assay graph node lane").split()


def _log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _append(path, rec):
    with open(path, "a") as fh:
        fh.write(json.dumps(rec) + "\n")


def make_prompt(target_tokens):
    """Filler prose of ~target_tokens, nonce-prefixed so prompt cache can't reuse."""
    nonce = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    n_chars = max(40, int(target_tokens * CHARS_PER_TOKEN) - 80)
    words, size = [], 0
    while size < n_chars:
        w = random.choice(_WORDS)
        words.append(w)
        size += len(w) + 1
    return (f"[{nonce}] Read the following notes, then reply with exactly one "
            f"short sentence summarizing their theme: " + " ".join(words))


def _request(body, timeout_s, stream):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + "/v1/chat/completions", data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {TOKEN}"})
    return urllib.request.urlopen(req, timeout=timeout_s)


def calibrate():
    """One non-streamed call: fit CHARS_PER_TOKEN to the server's real count."""
    global CHARS_PER_TOKEN
    target = 1000
    prompt = make_prompt(target)
    body = {"model": "gpt-oss-120b", "max_tokens": 8,
            "messages": [{"role": "user", "content": prompt}]}
    with _request(body, 300, stream=False) as r:
        usage = json.load(r).get("usage", {})
    actual = usage.get("prompt_tokens")
    if actual:
        CHARS_PER_TOKEN = CHARS_PER_TOKEN * actual / target
        _log(f"calibrated: target {target} -> actual {actual} "
             f"(chars/token now {CHARS_PER_TOKEN:.2f})")


def one_request(tag, prompt_tokens, max_tokens, timeout_s=1800):
    """Streamed request; returns record with ttft/decode measured client-side."""
    prompt = make_prompt(prompt_tokens) if isinstance(prompt_tokens, int) else prompt_tokens
    body = {"model": "gpt-oss-120b", "max_tokens": max_tokens, "stream": True,
            "messages": [{"role": "user", "content": prompt}]}
    rec = {"tag": tag, "t_start": time.time(), "prompt_target": prompt_tokens
           if isinstance(prompt_tokens, int) else len(prompt) // 4,
           "max_tokens": max_tokens, "ok": False, "ttft_s": None,
           "total_s": None, "chunks": 0, "error": None}
    t0 = time.time()
    try:
        with _request(body, timeout_s, stream=True) as r:
            for line in r:
                if not line.startswith(b"data: ") or b"[DONE]" in line:
                    continue
                try:
                    chunk = json.loads(line[6:])
                except ValueError:
                    continue
                if chunk.get("usage"):
                    rec["usage"] = chunk["usage"]
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                if delta.get("content") or delta.get("reasoning_content"):
                    rec["chunks"] += 1
                    if rec["ttft_s"] is None:
                        rec["ttft_s"] = round(time.time() - t0, 3)
                if choices[0].get("finish_reason"):
                    rec["finish_reason"] = choices[0]["finish_reason"]
        rec["ok"] = True
    except urllib.error.HTTPError as exc:
        rec["error"] = f"HTTP {exc.code}: {exc.read()[:200].decode('utf-8', 'replace')}"
    except Exception as exc:  # noqa: BLE001 — record every failure shape
        rec["error"] = f"{type(exc).__name__}: {exc}"
    rec["total_s"] = round(time.time() - t0, 3)
    if rec["ok"] and rec["ttft_s"] and rec["chunks"] > 1:
        rec["decode_tps"] = round((rec["chunks"] - 1) / max(rec["total_s"] - rec["ttft_s"], 1e-6), 2)
    _append(os.path.join(OUT, "requests.jsonl"), rec)
    return rec


def run_wave(tag, concurrency, prompt_tokens, max_tokens, timeout_s=1800):
    """`concurrency` simultaneous requests; per-request rows land in the JSONL."""
    _log(f"wave {tag}: c={concurrency} p~{prompt_tokens} out={max_tokens}")
    threads = [threading.Thread(target=one_request,
                                args=(tag, prompt_tokens, max_tokens, timeout_s))
               for _ in range(concurrency)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    _append(os.path.join(OUT, "waves.jsonl"),
            {"tag": tag, "concurrency": concurrency, "prompt_target": prompt_tokens,
             "max_tokens": max_tokens, "wave_wall_s": round(time.time() - t0, 2)})


def _hwmon_temps():
    temps = {}
    base = "/sys/class/hwmon"
    try:
        for hw in os.listdir(base):
            name_p = os.path.join(base, hw, "name")
            if not os.path.exists(name_p):
                continue
            name = open(name_p).read().strip()
            for f in os.listdir(os.path.join(base, hw)):
                if f.startswith("temp") and f.endswith("_input"):
                    try:
                        temps[f"{name}.{f[:-6]}"] = int(open(os.path.join(base, hw, f)).read()) // 1000
                    except (OSError, ValueError):
                        pass
    except OSError:
        pass
    return temps


def sampler(stop_event):
    path = os.path.join(OUT, "samples.jsonl")
    while not stop_event.is_set():
        rec = {"t": time.time()}
        try:
            req = urllib.request.Request(BASE + "/slots",
                                         headers={"Authorization": f"Bearer {TOKEN}"})
            with urllib.request.urlopen(req, timeout=4) as r:
                slots = json.load(r)
            rec["slots_busy"] = sum(1 for s in slots if s.get("is_processing"))
            rec["slots_total"] = len(slots)
        except Exception as exc:  # noqa: BLE001
            rec["slots_error"] = f"{type(exc).__name__}"
        try:
            mem = {}
            for line in open("/proc/meminfo"):
                k, v = line.split(":", 1)
                if k in ("MemAvailable", "SwapFree", "SwapTotal", "Cached"):
                    mem[k] = int(v.strip().split()[0]) // 1024  # MiB
            rec["mem_mib"] = mem
        except OSError:
            pass
        temps = _hwmon_temps()
        if temps:
            rec["temps_c"] = temps
        _append(path, rec)
        stop_event.wait(10)


def sweep_A():
    for c in (1, 2, 3, 4, 6, 8, 12, 16):
        for r in range(2):
            run_wave(f"A.c{c}.r{r}", c, 800, 128)


def sweep_B():
    for p in (512, 2048, 4096, 8192, 12288, 15000):
        run_wave(f"B.p{p}", 4, p, 128)
    _log("B: over-limit failure-mode cell (expect clean per-request error)")
    run_wave("B.overlimit.p17500", 1, 17500, 128, timeout_s=600)
    _log("B: prompt-cache pair (identical prompt, sequential)")
    cached = make_prompt(8192)
    one_request("B.cache.cold", cached, 64)
    one_request("B.cache.warm", cached, 64)


def sweep_C():
    run_wave("C.out256", 4, 256, 256)
    run_wave("C.out1024", 4, 256, 1024)
    run_wave("C.c8.out512", 8, 256, 512)


def sweep_F():
    run_wave("F.burst32", 32, 400, 96, timeout_s=2400)


def sweep_E(duration_s=600):
    t_end = time.time() + duration_s
    i = 0
    while time.time() < t_end:
        run_wave(f"E.soak.{i}", 4, 800, 128)
        i += 1


def sweep_D(duration_s=900):
    t_end = time.time() + duration_s
    i = 0
    while time.time() < t_end:
        run_wave(f"D.load.{i}", 2, 600, 128)
        i += 1


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "smoke"
    os.makedirs(OUT, exist_ok=True)
    _log(f"gambit mode={mode} out={OUT}")
    calibrate()
    stop = threading.Event()
    s = threading.Thread(target=sampler, args=(stop,), daemon=True)
    s.start()
    try:
        if mode == "smoke":
            run_wave("smoke", 1, 200, 32)
        elif mode == "all":
            for fn in (sweep_A, sweep_B, sweep_C, sweep_F, sweep_E):
                _log(f"=== {fn.__name__} ===")
                fn()
        else:
            {"A": sweep_A, "B": sweep_B, "C": sweep_C, "D": sweep_D,
             "E": sweep_E, "F": sweep_F}[mode]()
    finally:
        stop.set()
        s.join(timeout=12)
    _log("gambit done")


if __name__ == "__main__":
    main()

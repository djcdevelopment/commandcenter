# Fact sheet — the 2026-09-09 session. Every number here is from a receipt or a commit.

Ground rules for the script: no number appears that is not on this sheet. Numbers are SPOKEN
("two thousand one hundred twenty-eight"), not written as digits, in dialogue. File paths are
avoided in dialogue unless they are the point.

## The machine
- OMEN: Core Ultra 9 285K, Z890, 128 GB DDR5, two Intel Arc Pro B70 (~31 GB usable each,
  reported 32,558 MiB), Intel AI Boost NPU (arch 3720, ~13 TOPS), Arrow Lake iGPU (4 Xe cores).
- Production: Qwen3-30B-A3B Q4_K_M, llama-server on port 8082 under llama-swap, dual layer-split.
- Cards by PCI address: 0000:09:00.0 and 0000:04:00.0. The second is consistently the hotter one.

## Chapter 1 — the KV hunt
- Found `hearth/rotation/kv.py`: llama.cpp slot save/restore. Slot files carry NO model identity;
  identity lives only in the filename and a manifest.
- KV save/restore measured: 2.68 GB saved in 1.74 s, restored in 1.19 s. Re-prefill of the same
  prompt cold: 102.8 seconds. So restore beats re-prefill by roughly sixty to one.
- The RAM disk (T:, 8 GB) was specced in the LZ brief and never built until this session.
- LZ brief verdict on staging whole models on the RAM disk: IGNORE. Smallest model in the set is
  8.28 GiB; T: has 7.97 GiB free. Nothing fits.

## Chapter 2 — block 1, five perfect measurements of nothing
- Five repeats of the same cell: 2,334.0 / 2,284.1 / 2,285.1 / 2,188.6 / 2,285.5 jobs per hour.
  Mean 2,275.5. Spread 6.39%.
- All six gates green every time. Production measured at 98.6–99.5% of baseline throughout.
- THE DEFECT: across one cell's window the server processed 64 uncached prompt tokens and served
  3,073 from cache. Six requests times 440 tokens is 2,640. The prefill was a cache hit.
- Per request the server processed ~1 token in ~16 ms, reporting a "prefill rate" of about
  27,000 tokens/sec. The harness's own guard nulls that out, which is why every receipt's prefill
  read null and nobody noticed.
- Two more defects the same receipts exposed: gate 4 was measured over a span that included the
  harness's own single-stream probes (both slots busy read 0.50 over the span, 1.00 over the load),
  and per-poll slot samples were discarded so a 0.51-second wait could not be attributed.

## Chapter 3 — measure the premise
- `cache_prompt` is a llama.cpp NATIVE-endpoint field; the harness posts to the OpenAI-compatible
  endpoint. Whether it forwards was an assumption the whole re-run rode on.
- Two arms, four identical requests each. Control: 453 tokens processed, then 1, then 1, then 1.
  With the flag: 457, 457, 457, 457. Uncached fraction 0.25 vs 1.00. Prefill 10.7 ms vs 224.5 ms.
- That yielded the first real single-stream prefill number: 457 tokens in 224.5 ms, about
  2,035 tokens per second.
- Registered before the re-run: jobs/hour would land 5–8% below block 1, near 2,100–2,160, p50
  about 3.35 s. Actual: 2,097.4 and 3.344 s.

## Chapter 4 — 222 milliseconds and the three modes
- Block 2 (real prefill): 2,097.4 / 2,100.0 / 2,203.7 / 2,125.2 / 2,115.2. Mean 2,128.3.
- Spread 5.00%, but p50 spread 2.26% and decode 2.14% while p95 spread 10.45% — all tail.
- Four of five repeats have exactly ONE round where the server launched the two concurrent requests
  222.0, 222.4 or 250.7 ms apart instead of 0.2 ms. Client-side send skew: 0.0–12.5 ms. Server-side.
- THREE PREFILL MODES, cleanly separated with empty space between: 1,982–1,995 tok/s alone;
  1,840–1,890 prefilling beside the other slot's decode; 1,460–1,476 batched with the other prefill.
- Batching is worth 10.1%: fully batched cells average 2,199.5, fully unbatched 1,998.5.
- MY REFUTED HYPOTHESIS: I supposed my own rate probe was holding a slot. The log showed the lone
  warm request released 24–32 ms BEFORE the measured pair launched. Wrong, recorded as wrong.

## Chapter 5 — the rung is never idle
- A deliberate 200-second no-traffic window still read rep-1 at 106.71 against 106.26 warm.
- Reason: a one-token completion arrives every ~31 seconds. Confirmed in the keep-alive log:
  04:29:28, 04:29:59, 04:30:30, 04:31:01, 04:31:32 — exactly 31 seconds apart.
- I wrote it up as "untestable as configured". WRONG. Derek: he set it up on fx99 to keep
  performance up. Host is `ai-1` at 192.168.12.220, two systemd timers, passwordless ssh.
- FOUR sibling probes in the same campaign family already stop and start those timers.

## Chapter 6 — I broke production
- With the timers stopped the idle was real: a single 299-second gap in the keep-alive log.
- Cold reads: 41.97, 28.00, 28.19 tok/s against a 106.0 baseline. About 33%.
- Prediction was an unwarmed rep-1 at 65–90% of warm. Refuted on the severe side.
- 24 sustained concurrent requests moved it 33% → 48% and left it unstable: 72.0, 50.06, 30.44,
  each rep LOWER than the last, 81.8% spread.
- ONE restart: 106.45, 106.25, 106.19 — 100% of baseline, 0.24% spread, first warm iteration.
- MY PROBE'S OWN BUG: it compared the cold reading to a "warm" reference measured six requests
  later that was itself still collapsed, and printed "no decay observed". Right measurement,
  wrong verdict.
- Cost: about seven minutes of degraded production. No real traffic affected — the gateway ledgers
  carry no dispatch in that window.

## Chapter 7 — vllama, June
- `E:\work\vllama`: .NET 9 lifecycle wrapper and OpenAI alias facade over llama-server, six commits,
  2026-06-04 to 06-17. 1,569 lines of C#.
- ADR-0007 Decision 1: "readiness means can serve, not process up" — enforced by a real one-token
  generation through the resolved alias. Written from a judge that returned 503 mid-run while
  healthy. THREE MONTHS before this campaign re-derived the same law.
- ADR-0006: a 22 GB host-RAM preflight floor, tied to an OOM cascade that once cost a BIOS reflash.
- THE UNUSED LEVER: vllama launches with q8_0 KV quantization. Production runs f16 —
  6,144 MiB of K plus 6,144 of V. q8_0 would free about 3 GB per card.
  ⚠ AND IT IS NOT FREE — priced 2026-09-09 against our own FF4 measurement (claim register #29):
  q8_0 costs 4.7% of decode (33.23 → 31.68, reproducible to 0.4%) and buys no prefill. It is a
  CEILING lever, never a speed lever. Stranger still, q4_0 decode BEATS q8_0 by 1.9% — the wider
  type is the slower one on this Vulkan path. An episode that calls this lever free is wrong.
- It would not run today: its config points at a D: drive that no longer exists.

## Chapter 8 — the wrong variable
- Derek's pivot: "we're performance testing now instead of R&D verification".
- The client sweep: 1 client 1,514.1 jobs/h; 2 clients 2,128.3; 4 clients 2,127.1; 8 clients
  2,163.6. Flat within 1.7% from two clients to eight. p95 latency 2.387 → 3.552 → 7.006 → 13.684 s.
  Duty 0.0 at every point, boards at ~73 W against a 160/114 W reference.
- PR 27652 (Derek's, open upstream) replaces llama.cpp Vulkan's hardcoded
  `mul_mat_vec_max_cols = 8` with a runtime environment override. Past that width the decode
  dispatch falls onto matmul: measured on a B70 at 40.7 s/pass against MMV's 3.1–5.8 s.
- Production ALREADY runs the patched binary AND the launcher already exports the window at 16.
  Production ran two slots. Eight times of purchased, unused headroom.
- The launcher's own comment: the knob "clamps silently above 16".

## Chapter 9 — a task named Restart that only stops
- The runner edited the config, ran the restart task, and failed at the ready marker.
- Production was DOWN: no listener on 8081 or 8082, serve log frozen.
- The launcher's own header states the procedure: the restart task is stop-only; delete the
  sentinel, then run the BOOT task. That brought llama-swap back at once and production ~3 min later.
- An earlier restart in the session appeared to self-recover — consistent with the boot task's
  retry count firing, not with the restart task restarting anything.
- KV does not scale with slots: 12,288 MiB at two, four and eight slots alike — 65,536 cells at
  two seqs, 32,768 at four, 16,384 at eight. Compute buffers FELL: 712 → 456 → 328 MiB per card.

## Chapter 10 — fifty-eight percent
- Four slots: 2,918.1 jobs/h at four clients, 1.37× the two-slot ceiling, and p95 FELL to 4.944 s.
- Eight clients on four slots: 2,878.7 — same ceiling, double the latency.
- Four-slot block of three: 2,918.1 / 2,612.1 / 2,776.3, and delayed rounds 0 / 3 / 1. Inverse.
- Eight slots: 3,358.2 jobs/h. 1.58× the two-slot ceiling. Aggregate decode 133.6 → 177.2 → 203.2
  while per-request decode fell 66.8 → 44.3 → 25.4.
- FIRST NON-ZERO DUTY of the campaign: 0.0615 on the hotter card at four slots.
- Sixteen clients on eight slots: duty 0.632, sustained 154.68 W, peak 181.19 W — above the
  159.92 W reference the metric is calibrated on. Throughput unchanged at 3,407.2.
- The split is lopsided: 154.7 W on one card against 81.5 W on the other, under a flag that is
  supposed to divide the model evenly.

## Chapter 11 — valve float
- Sixteen slots: 2,206.9 and 2,439.5 jobs/h. Mean 2,323. About 32% WORSE than eight slots.
- Per-request decode collapsed 25 → 8. Aggregate decode fell 202 → ~135.
- Admission skew grew all afternoon: ~222 ms at two slots, 434–589 at four, 674–943 at eight,
  1,865–2,932 at sixteen.
- Power went spiky rather than sustained: peaks of 166–183 W over a p50 of only 66–73 W.
- Derek's prior: he'd already swept the step matrix; sixteen is best for this hardware for most of
  his use cases. This bounds it — for THIS model at THIS prompt size the peak is eight.

## Chapter 12 — the cliff under load
- At sixteen slots, window widened to 16: 2,206.9 and 2,439.5. Window at the stock 8: 1,953.6 and
  2,059.5. Means 2,323.2 vs 2,006.6 — 15.8%, and the two arms' ranges do not overlap.
- Per-request decode separates the same way: 8.1/8.8 against 7.1/7.5.
- Same binary, same model, same slots, same prompts. Only the environment variable changed.
- This is the first time the knob has been measured under llama-server concurrency. The knee
  campaign used llama-bench and frame pacing. The maintainer named per-vendor serving data as the
  blocker on the pull request.

## Chapter 13 — the weights were already in RAM
- llama-server's private commit: 30.31 GiB. Its own load report: 17.28 GiB of weights, 12.00 of
  KV, 0.78 of compute buffers = 30.06 GiB. Apart by 0.8%.
- Every byte on the cards has a host-memory commitment behind it, because the driver needs
  somewhere to evict to.
- Flash-Next MoE: 48 layers, 512 experts per layer, top-10 routed plus 1 shared, 2,465,280 bytes
  per expert. That is 1.21 GB of expert reads per token with no reuse.
- At the measured 13.3 GB/s pinned host-to-device rate, the ceiling is 11.0 tokens/sec.
  The NPU's projected rate was 10.9–12.6. It was bus-bound, not compute-bound.
- CPU reading DDR5 directly measured 14.4–14.7 tok/s, implying about 17.6 GB/s — faster than the bus.
- THE NPU CLOSURE USED THE WRONG BASELINE: it compared against 23–26 tok/s, which is a different
  model's number. Like for like, the NPU is 75–88% of CPU, not half.
- Flash-Next is NOT infeasible: its quarantine was a JSON-schema grading failure on two of eight
  tasks. All eight returned success.

## Chapter 14 — the patch that Windows ate
- `E:\work\herm`, June 2026: Derek's attempt to run the Hermes agent framework against vllama on
  Windows. 21 files, written in a single six-hour window.
- Upstream's own module docstring: POSIX-only, depends on fcntl and termios, "on native Windows,
  importing this module raises ImportError and the dashboard shows a WSL-recommended banner".
- The patch: 608 lines adding a complete Windows ConPTY backend via pywinpty.
- Failure modes it records: passing the full argument vector where the library wanted
  arguments-only duplicated the program name, so node received the executable's DOS header and
  died with "MZ ... SyntaxError". WSL2 reporting 131,072 columns, overflowing a sixteen-bit field,
  raising the wrong exception type and surfacing as blank text. The library raises on EOF rather
  than returning empty. No real SIGKILL. CreateProcess does not search PATH.
- THE PUNCHLINE: the patch file itself is UTF-16 with a byte-order mark — mangled by PowerShell's
  default redirect. The artifact documenting Windows encoding pain was corrupted by Windows
  encoding. And the same bug bit this session: a byte-order mark rode into a git commit subject.
- It was never upstreamed. Upstream's docstring calls Windows ConPTY "a future enhancement".
- The proof it worked: nine markdown chapters of fiction the agent wrote, about a machine waking up.

## Chapter 15 — I don't hear any fans
### CORRECTION found after the fact sheet was first written — the 90 C IS on record
- Derek said imagegen bounces off 90 C. The engineer said nothing had ever recorded it.
  **That was wrong.** The imagegen lane writes three telemetry snapshots per job into its receipts:
  job start, a ten-second poll peak, and job end. Across 2,588 receipts:
  - VRAM on the bus-4 card: p50 80 C, **max 92 C**. On bus-9: p50 80 C, max 84 C.
  - GPU tile: max 78 C on both cards.
- So the operator's felt number was accurate to within two degrees, and the data to prove it had
  been sitting in the receipts the whole time — never assembled, never plotted, never compared.
- What genuinely does NOT exist: a continuous stream, and any power measurement at all.
  `power_watts` is **null in 12,940 of 12,940 telemetry samples**. `throttled` likewise null in all
  12,940. `utilization_percent` reads **zero in all 12,940** — the counter is read and never lands.
- Reason power is missing: the probe captures ONE tick per sample. Watts require two consecutive
  energy readings to difference. One snapshot yields nothing.
### The real shape of an imagegen run
- Epoch 37, the longest: **3.59 hours, 850 images, 6.83 GPU-hours of work, duty 1.90 out of a
  possible 2.0** — both cards about ninety-five percent busy for three and a half hours.
- Epoch 33: 3.40 hours, 481 jobs. Epoch 3 held the fence longest at 5.20 hours, mostly idle.
- Per job across all 2,588 receipts: median 24.85 seconds, p90 30.26, max 400.8. 2,543 succeeded,
  37 failed, 8 cancelled. Total recorded work: 17.69 GPU-hours.
- The model is not SDXL and not Flux: `z-image-turbo` on 2,580 of 2,588 jobs. Twenty-six sampling
  steps on the big campaigns, about 1.11 iterations per second at 864 by 1536.
- Lane balance near perfect: 1,296 jobs on one card, 1,292 on the other. Every job single-card.
### Why imagegen and inference cannot share
- Enforced in code three ways, not advisory: the door closes both inference rungs while the fence
  is held (pinned calls included), rotation's first gate fails closed, and starting an image
  session physically drains and stops llama-server, refusing to proceed if it survives.
- The stated reason is memory arithmetic, not a measured failure. The image model declares about
  26 GiB on a card that reports roughly 31 GiB total; production holds about 15 GiB per card.
- NOT MEASURED: nobody has ever run both at once and recorded what happened.
### The one that got away
- Non-forced drain is gentler than forced, not lossless. Fifty-one image jobs have expired in
  queue across the corpus; eight of them died during one handover, twenty-one minutes after the
  pool had already gone back to inference.
### For the capture
- b70tools supports `--ticks 0` — unbounded, stops on interrupt. Confirmed in its C++ source.
- About 4.4 KB per tick, so four hours at one-second cadence is roughly 64 MB.
- **Board power is not obtainable at any capture length**: the card-level energy counter is emitted
  exactly once per capture. Only GPU-tile power can be differenced. Say so on any receipt.
- Idle floor for reference: 26.67 and 26.60 W per card with the model resident and nothing running.

## Chapter 15 — original notes
- Derek: imagegen turns the machine into a small star after three to four hours and bounces off
  90 °C after thirty minutes, even in cool weather.
- The reference every duty number is measured against is the p50 of a ~92-SECOND prefill burst at
  two clients. ⚠ CORRECTED 2026-09-09 from "twenty-second": twenty was the AMBIENT window; the
  burst is 90 s declared and 92 one-second intervals measured, read from the frozen receipt
  (`ref-20260909T085437Z`). The point survives the correction and gets sharper — 92 seconds still
  stands in for a 3.59-hour workload, ~140x the window. See claim register #30.
- No sustained-load capture exists on this box. Every thermal record is a single-tick snapshot or
  a run under five minutes. The longest is about 260 ticks.
- Measured across four cells inside ninety minutes: idle VRAM baseline swings 56–62 °C while the
  workload adds only 0–4 °C (VRAM) and 7–8 °C (GPU). Absolute temperature is dominated by ambient.
- Our peak all day was 68 °C. Derek's limit, set by him: 95 °C.
- The render lane already solved the sibling problem: `accepted_lane_count` is a MEASURED
  coexistence result — two lanes accepted, inference impact −1.34% / +0.87%.
- `propose_schedule` has never been called. Zero times in 243,411 ledger events.

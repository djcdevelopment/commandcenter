# FACT SHEET — The Honesty Engine

Every figure the chapters use, with its regime and its receipt. **If a number is not here, it is not
in the script.** Where a claim was corrected, both the original and the correction are listed,
because the correction is the content.

---

## Chapter 1 — Seven Hours, Zero Numbers

- Commits: `4efe332`, `547c060`, `35f0a87`, `9e71f75`, `c99d4dc`, `2cb0c01`, `e3860d8`, `2e9c18b`.
- `-c` is the TOTAL and the build divides it: 8 slots × **16,384** tokens at `-c 131072`, against
  **65,536** per slot at `-np 2`. The server's own log reports `n_ctx_slot = 16384`.
- Door budget `context_bytes` **229376 → 57344**, at ~3.5 bytes/token. `parallel_slots` **2 → 8**.
- The silent-truncation measurement, quoted from the config's own comment: **2026-07-18**, a
  **17.5k-token** request against a **16k** slot **returned ok with normal timing**.
- Nothing dispatched to the rung between the change and the fix. **A near miss found by audit, not
  an incident.**
- The trade, stated: the August widening existed for an agent requiring a 64,000-token floor.
  Narrowing breaks that floor. Loud refusal beats quiet truncation.
- Live verification: a pinned **60,000-byte** payload is refused naming the budget and rung;
  **50,000** routes through and reports `global_parallel_slots: 8`.
- Capacity-bucket trap: `submit_render` p50 **48.6 ms** / p90 **54.7 ms** over **17** calls;
  `submit_image` p50 **81 ms** over **2,917** calls. Both time the door call, not the job. Real
  image-lane durations: median ~**25 s**, p90 ~**30 s** across **2,588** jobs.
- Fall-through default is **600 s** — a coarse guess, but a schedulable one.

## Chapter 2 — The Label That Named a Dead Epoch

- The door reported baseline **105.33 tok/s** (set 2026-09-09) under an epoch label naming
  **2026-08-29T18:22**.
- Cause: `--set-baseline` wrote `baseline_decode_tok_s`, `baseline_set` and `baseline_note`, and
  never `baseline_epoch`.
- ⚠ **CORRECTION TO SAM'S FIRST REPLACEMENT LABEL.** He wrote that the epoch was opened by the slot
  change. The cell receipts say the sweep ran **2 → 4 → 8 → 16** and the last `-np 16` cell finished
  **07:04:26**. The epoch began **07:05:18** — opened by the **RESTORE to 8**, not the change to 8.
  Baseline set **07:06:00**, 42 s later.
- Epoch start is DERIVED: the server runs under a scheduled task whose start time is inaccessible
  and whose process counters read zero while it works. Method: log's elapsed stamp subtracted from
  file mtime. Corroborated independently by the running server reporting `n_ctx_slot = 16384`.
- `epoch_boundaries` held one row (the 2026-09-03 cutover) and nothing since. The current boundary
  had never been recorded.
- Fix `2e9c18b`: an optional `--epoch`, a derived label otherwise, the previous label never carried
  forward, and the standing comparability warning preserved. **7 tests, one against the live file.**

## Chapter 3 — The Experiment That Had Already Answered

- ⚠ **THE CORRECTED CLAIM:** replica-per-card "has never been measured — quarantined at 96 °C before
  producing data." Written into claim register #28, the plan, AND the previous episode. **False.**
- Source read directly after two research agents disagreed:
  `corpus/backfills/qwen38-campaign-full-20260827.bench-row.v1.jsonl`. **48 replica rows exist.**

| clients | replica | dual-split | delta | replica n | citable |
|---:|---:|---:|---:|---|---|
| 1 | **410.09** jobs/h | 396.75 | **+3.4%** | 3 req / 26.3 s | yes, thin |
| 2 | **819.48** jobs/h | 636.06 | **+28.8%** | 6 req / 26.4 s | yes, thin |
| 4 | 736.53 | 619.67 | +18.9% | **10 valid of 12** | ❌ **aborted cell** |

- Decode: replica **23.05 / 23.10** flat; dual-split **22.97 → 18.70**.
- Regime: **Qwen3.8-27B dense**, 512-token prompts, `-c 65536 -np 1` per server, MTP off. **Not**
  the 30B mixture model at `-np 8`.
- The dual arm ran its full matrix (**633.3** at 8 clients, **656.5** at 24). The replica stops at 2.
  **The partition's curve past 2 clients is unknown.**
- The quarantine wrote **66** skipped-cell rows. Reading the quarantine and not the backfill is the
  likely origin of "never measured".

## Chapter 4 — A Gate That Scores the Corpse

- Gate 8 runs **after** the telemetry stream self-terminates. There is **no live temperature poll**
  in the runner. `thermal_exceeded` is **absent from the sweep-halt list** — a 95 °C cell is marked,
  excluded, and the next cell launches.
- The 2026-08-27 abort was caught by a **live 10-second watchdog** with a **80 °C resume line**.
  That watchdog no longer exists.
- The real series, `0000:04:00.0` VRAM, ~12 s apart: **78 → 88 → 92 → 94 → 96**, 06:39:56 to
  06:40:44. **48 seconds**, still climbing ~2 °C per tick when cut.
- ⚠ **CORRECTION BY TEST.** Sam wrote the slope rule fires "~24 s" earlier, from eyeballing the
  table. The test replayed the real series: it fires on the **78 → 88** step at **06:40:08** —
  **36 seconds** before 96. The code now says what the test measured.
- A separate test covers the case the slope rule is designed to miss: a 2 °C-per-reading climb never
  trips it, and the absolute limit still catches it.
- Thresholds: abort **95 °C**, warn **88 °C**, both counters, both cards. Derek's call, his words:
  *"if we hit that, we back off. i've cooked these cards plenty of time, they'll be fine."*
- `0000:04:00.0` VRAM is the binding counter — **4–8 °C above its sibling** under identical load
  across every dataset. At the abort its own GPU tile read **75 °C** while its VRAM read **96**.
  **No GPU tile has ever exceeded 81 °C on this box.**
- Watchdog: **28 tests**; kills by recorded pid only; blindness returns `blind` and stops the run.
- ⚠ See Chapter 7: this watchdog had a defect that only appeared when it was wired into a real cell.

## Chapter 5 — The Drone Photographer

- Operator's description: *"a series of drone photographs of buildings by teleporting around the
  world and then taking multiple angles from each location."* **Not gameplay.** He expected very
  light overhead.
- Baseline **before** launch: `0000:04:00.0` **14.489 GB / 56 °C**; `0000:09:00.0` **15.587 GB /
  54 °C**. Production resident and healthy at **106.00 tok/s**.
- After: lands **entirely on `0000:09:00.0`** (+**1.717 GB**). `0000:04:00.0` **unchanged**. The
  iGPU reports nothing. **No tenancy fence taken**, so production kept serving.
- Capture: **98.5 minutes**, **99** one-minute buckets, ~4.1 KB/s of writes.

| card | tile power p50 | VRAM first → last | VRAM max |
|---|---:|---|---:|
| `0000:09:00.0` | **112.23 W** | 70 → 74 °C | **78** |
| `0000:04:00.0` | **26.65 W** (flat, every bucket) | 56 → 58 °C | 60 |

- **THE FIRST THERMAL PLATEAU MEASURED ON THIS BOX:** VRAM plateau **72 °C at t+1 min**, GPU tile
  **72 °C at t+9 min**. Peaks 78 and 77, against a 95 °C limit.
- Instrument cross-validated: **26.65 W** matches the independently known idle-with-model-resident
  floor (~26.5–26.8 W).
- The rung ran at **50–56%** of baseline while `0000:04:00.0` sat at the idle floor for all 98
  minutes. Mechanism: layer-split spans BOTH cards, so a tenant on either taxes the whole engine.
- ⚠ Registered as a **prediction (Q7)**, not a claim: 2 clean samples, and the tool requires 3.
- ⚠ **NOT a saturation reference.** A game on one card beside a mostly-idle model reads well below
  the frozen **159.92 / 114.05 W** burst p50. Frozen **beside** the burst, never over it.
- ⚠ Board power is **unobtainable at any capture length** — `card.energy_j_counter` is emitted once
  per capture. All watts here are **tile** watts.

## Chapter 6 — Three Empty Files Named After My Own Prose

The six refusals, in order:

1. The rate checker printed `*** FAIL: 55% of baseline. This is DEGRADATION, not noise.` — right
   about the sample, wrong about the machine. Spread **21.83%** (contention) vs **0.34%** (healthy).
2. A low sample coincided with Sam's own full test-suite run. Recorded as a caveat, not dropped.
3. The series loop used a cmd `timeout /t`, which **fails under redirected stdin** and fired **15
   iterations in one second**. The sampler's **60-second spacing guard refused 14 of 15**. One extra
   sample taken; the operator's machine unaffected. Rewritten in PowerShell.
4. The sampler refused a steady-state claim from **2 clean samples**, needing **3**.
5. A process hunt matching on command line **matched its own query**, never converged, and killed
   Sam's own tool shells. Precedent on this box: a kill by command-line match once took down **three
   production services**. Settled by excluding `$PID` and its parent. Nothing had been running.
6. Backticks in a shell-built string ran fragments of prose as commands; `->` arrows in sentences
   were read as redirections. Result: a memory note with **holes where every file path should be**
   ("Plan: ." / "I read myself.") and **three zero-byte files** in the repo root named `what`,
   `*better*`, and `**This`, timestamped **12:15:17**. Deleted by exact name, never by pattern.

**Full tenant series (8 samples):** 106.00 (101%, 1.91%) · 57.85 (55%, 21.83%) · 104.35 (99%, 0.34%)
· 63.52 (60%, 20.47%) · 53.58 (51%, 3.11%) · 58.88 (56%, 4.17%) · 29.78 (28%, 32.61%) · 50.91 (48%,
50.65%).

**Suite through the session:** 3,155 → 3,162 → 3,190 → 3,206 passing.
**Claim register rows added or corrected today:** 25, 26, 27, 28 (corrected), 29, 30, 31, 32, 33.

## Chapter 7 — The Seventh Refusal

- Commit `405317a`. The live guard polls the growing stream every **5 s** during the load and
  terminates the load **by its own pid**. Blind is tolerated for a **90 s grace period** because a
  cell's stream starts moments before its load.
- `thermal_exceeded` **added to the sweep-halt list**. It was absent: a cell at 95 C was marked,
  kept, excluded from the surface, and **the next cell launched onto the hot card**. The halt now
  prints the **80 C** resume line the 2026-08-27 harness enforced — printed, not enforced, because
  resuming is Derek's call about his own cards.
- ⚠⚠ **THE DEFECT THE WIRING FOUND.** The watchdog aged out a card whose temperature had not been
  reported for **90 s** and called it **blind**, and blind stops a run.
- **Why that is backwards:** the counters emit **ON CHANGE**. A stable card correctly reports
  nothing. Measured on the same day's 98-minute capture: the idle card produced **13 GPU readings
  in 98 minutes** — about one every 4–8 minutes — while healthy at **26.6 W**. The rule would have
  declared it blind almost continuously and **killed every long cell**, including the soak cell it
  was built to protect.
- **Why 28 tests missed it:** every test evaluated the state **once**, against freshly written data.
  A single evaluation never ages anything out. There was a passing test named "stale readings are
  blind" — it verified that the wrong mechanism worked.
- **Fix:** liveness moved from temperature recency to **stream growth**. Energy ticks every second,
  so a growing file is the honest signal. A quiet temperature is stability; a static file is a dead
  collector. Regression test simulates **10 minutes** of a stable card emitting no temperature and
  asserts the verdict stays `ok`.
- Verified against the real **24 MB** capture: guard returns `ok` and identifies the hottest counter
  at **74 C**, matching the reduction's independent figure.
- **14 new tests. Suite green at 3,216.**

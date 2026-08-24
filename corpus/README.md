# corpus — benchmark provenance for a fleet whose parts move

**Status:** foundation landed and exercised; the adapter layer is **not built**.
Written 2026-08-20, committed 2026-08-24.

## What this is

Measurement-provenance tooling for the local hardware lab. It exists because the
estate's parts are fluid — cards move between boxes, boards get swapped, drives
get carried across machines — and **every one of those moves silently invalidates
a benchmark result that was labelled from config instead of from the hardware.**

Each module is a response to a specific way that went wrong on this estate:

| module | the failure it prevents |
|---|---|
| `fingerprint.py` | A `hardware_profile_id` typed into config goes stale silently. `omen-rtx5070-2026H2` was live on a box with **no NVIDIA card in it**. Captures identity per run instead; `hw_id` hashes board + CPU + RAM class + GPU set, and deliberately does *not* hash driver/BIOS/OS — those are what you vary *within* one machine identity. |
| `vkdevices.py` | On AM4 the two B70s enumerated as `Vulkan0`/`Vulkan1`, so every script in `E:\work\battlemage` hardcoded those indices. On the Z890 board the **iGPU enumerates first**. Those scripts do not fail — they benchmark integrated graphics and report the numbers under a B70 label. llama.cpp also believes the iGPU has 103 GB free, so a 59 GB model "fits" off system RAM and lands in the corpus as a dual-B70 measurement. Resolves devices by identity; refuses to return an integrated device unless asked. |
| `runlog.py` | Five months of results whose hardware, engine build and flags have to be reconstructed from prose afterwards — if they can be at all. Writes a manifest *before* the first number. A failed run still gets one, with `status: failed`: **a failed 120B load on a 128 GB host is a result.** |
| `verdict.py` | `b70tools verdict` refuses to proceed above a compiled-in 26.00 GB host RAM — calibrated for AM4's 32 GB (81%). On a 128 GB host that fires at **20% utilisation** and calls a healthy idle machine `broken`. Re-derives the judgement from the same `events.jsonl` with thresholds from the host actually present. Replaces one constant, not the instrument. |

The common thread: **a silent wrong-hardware substitution is the most expensive
kind of measurement bug, because nothing about the output looks wrong.**

## Why it is dated the day of the motherboard swap

It is not incidental to that swap — it is the reaction to it. The OMEN board was
replaced on 2026-08-20 (Core Ultra 9 285K / 128 GB / 2× Arc Pro B70, no NVIDIA),
and this package was written in a ~12 minute burst that evening because the new
board broke the index and threshold assumptions the old one had made safe.

`vkdevices.py` encodes the same finding that is hardcoded as
`GGML_VK_VISIBLE_DEVICES=1,2` in `fleet/arcserve/serve-arc.cmd`, whose comment
reads *"LOAD-BEARING. Never remove it; re-verify indices after driver updates."*
**This is the tool that performs that re-verification** instead of trusting a
comment to be re-read.

## What is here, and what is missing

```
fingerprint.py  vkdevices.py  runlog.py  verdict.py   # built, executed, stdlib-only
schema/run-manifest.v1.json                           # implemented by runlog.py
schema/bench-row.v1.json                              # ⚠ contract only — NO implementation
adapters/                                             # ⚠ EMPTY STUB (0-byte __init__.py)
runs/b0-preflight-20260821T032313Z/                   # one real, complete run
```

**The gap is `adapters/`.** `bench-row.v1.json` defines the normalized measurement
row that would let six different harness formats (llama-bench, b70tools,
ollama-backend-lab, denning, hearth experiment rows, the AM4 gambit) be compared
in one shape. Nothing writes that row yet, so **zero `bench-row.v1` records exist**.
That is the package's actual deliverable and it is unbuilt — treat the schema as a
design note, not a working contract.

## Provenance of the committed run

`runs/b0-preflight-20260821T032313Z/` is genuine and complete (`status: complete`,
`failure: null`): a 300-tick / 1000 ms-cadence idle capture, 3,364 JSONL lines,
plus adapter enumeration. Its `manifest.json` `machine` block was captured live and
names the Z890 board and both B70s at PCI `0000:04:00.0` / `0000:09:00.0` with no
NVIDIA device present — which is what dates the package to *after* the swap by
direct evidence rather than by mtime.

Note the directory name is UTC (`20260821T0323Z`) while the files are 20:23 local
(PDT) on 2026-08-20 — the same instant, not two dates. `idle-collector.log` is not
committed; the repo's `.gitignore` excludes `*.log`.

## Usage

```
python -m corpus.fingerprint            # human summary
python -m corpus.fingerprint --json     # for run manifests
python -m corpus.fingerprint --id       # just the hw_id
python -m corpus.vkdevices              # resolved device table
```

Stdlib-only, per the repo rule. Exit codes in `verdict.py` follow b70tools'
convention: `0` healthy, `2` refuse to proceed.

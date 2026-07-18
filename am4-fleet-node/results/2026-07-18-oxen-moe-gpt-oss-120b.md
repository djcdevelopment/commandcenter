# oxen-moe — gpt-oss-120b resident on dual Arc Pro B70 (AM4), live as a HEARTH rung

2026-07-18 · receipt `br-20260718-064639-50a1d2f7` · merged `384d9fb` · follows
[O1 P2P microbench](2026-07-18-o1-ze-peer-p2p.md)

## What this is

Derek's cue: make gpt-oss-120b a **resident mechnet asset** — the always-warm big-MoE
router/worker on the two B70s, with queue-and-wait semantics and slot/KV polling to manage
goodput. O1's link data supported the shape (layer-split activations are noise at ~6 µs;
copy engine free); this run built it end-to-end.

## Model + storage

* `ggml-org/gpt-oss-120b-GGUF` MXFP4, **single file 63,387,346,208 bytes** (verified
  against the HF manifest), at `am4:/mnt/win/work/models/gpt-oss-120b/`.
* Downloaded in **14 min @ ~68 MiB/s** via a `systemd-run --user` unit. The 4TB is
  fstab-`ro` by design: Derek opened a remount-rw window + ACL grant on the subdir
  (`ntfs3` mount enforces NTFS ACLs — plain `derek` writes are denied even rw).
  **Serving needs only read-only mmap — the disk can return to `ro`.**

## Serving stack

`~/baseline/serve-moe.sh` → `b70-moe.service` (systemd `--user`, enabled; linger on →
boot-persistent), llama-server SYCL build `a3900a6`:

```
-m gpt-oss-120b-MXFP4.gguf --alias gpt-oss-120b
-ngl 99 -dev SYCL0,SYCL1 -sm layer -ts 1,1 --n-cpu-moe 4
-fa on -fit off -ctk q8_0 -ctv q8_0 -c 65536 -np 4
--slots --metrics --jinja --host 0.0.0.0 --port 8082 --api-key $AM4_OXEN_TOKEN
```

* `--n-cpu-moe 4` is **required, not tuning**: 63.4 GB of weights exceed 2×30.3 GiB usable
  VRAM; ~4 layers of experts ride host mmap (the D2 zram/swap landing is the backstop).
* `:8080/:8081` untouched — `b70-planner`/`b70-critic` remain the managed claimants of
  those ports (see residency handover below).
* Host-RAM preflight (used-ceiling 27 GiB) guards launch ordering, per the runbook.
* ufw: `:8082` allowed from `192.168.12.0/24` only (Derek's hand; same LAN-scoped shape
  as the facade's `:8090` rule — machine lanes stay off the tailnet, ADR-0014).

## Numbers

| Metric | Value |
| :--- | :--- |
| Cold load (ntfs3 mmap → VRAM) | ~3.2 min to health-OK |
| First-request prefill | 8.4 tok/s (one-time SYCL kernel warmup — not steady state) |
| **Warm prefill** | **221.6 tok/s** (415-token prompt) |
| **Decode, single stream** | **26.6–28.7 tok/s** |
| Concurrent decode (4 slots) | 4×120 tokens in 15.5 s ≈ 31 tok/s aggregate |
| Queued 5th request (wait-in-line) | completed at 20.1 s (+4.5 s wait), no hang, no error |
| Unit restarts across soak | 0 |
| Host RAM steady state | 12 Gi used / 18 Gi available |
| Per-card VRAM enumerated | 31,023 MiB |

A 14k-token payload (the rung's declared budget) prefills in ~65 s warm — workable for
`files=`-packed door offloads; typical calls are far smaller.

## Goodput design (the "wait in line + poll KV" ask)

* llama-server's `/slots` is the goodput signal. This build exposes `is_processing` +
  `n_ctx` per slot but **no `n_past`** → `kv_used_frac` reports `None` (dormant telemetry;
  slot saturation is the hard signal — enrich later via `/metrics` or a newer build).
* `hearth.toolsurface.occupancy.probe_moe_slots` (registry `_PROBES`): all slots
  processing (or KV >90% when the field exists) → `busy` → **opportunistic traffic steers
  to omen/flash**; unreachable/loading → `unknown` (fail-open: opportunistic skips, pins
  proceed). **Pinned calls dispatch regardless and wait in llama-server's internal
  queue** — wait-in-line comes free from the server, proven in the 5-vs-4 soak.
* Saturation observed live mid-soak: `slots_total=4 busy=4 idle=0`.

## Residency handover (decision)

gpt-oss-120b soaks both cards, so it **replaces b70-planner/critic as the steady-state
LLM tenant**: the opportunistic tags (`big-context`/`research`/`second-opinion`) moved
from `am4-oxen` to `am4-moe`; `am4-oxen` is **pin-only** and its auto-`revive` is removed
(a watchdog reviving the planner into the moe's VRAM would crash both). Deliberate
planner runs: stop `b70-moe`, then `wake_am4`. Imagegen windows likewise require freeing
the cards — the "mechnet modes" scheduling primitive (GpuLaunch/Allocator seed) is the
designed follow-up.

## Live proof through the door

Unpinned `local_generate(task="research")` after gateway restart (`384d9fb`, 527 tests):

```
backend: am4-moe · routed_by: tag:research · occupancy: available
model: gpt-oss-120b · endpoint: http://192.168.12.233:8082 · 163 tokens / 6.4 s
```

The mechnet ladder now reads: **am4-moe resident brain → omen-ollama (fast small-model
swaps, 12 GB) → gcp-gemini (trial credits) → fleet backlog.**

## Follow-ups

1. KV telemetry: surface `kv_cache_usage_ratio` (and `requests_deferred` queue depth)
   from `/metrics` into the probe when wanted — the probe's registry seam is ready.
2. Session parking: llama-server slot save/restore for idle-session KV rotation
   (~100–150 ms restore over the measured 14.3 GB/s link vs re-prefill).
3. Mechnet modes: scheduled moe-resident ↔ imagegen window switching.
4. Capacity facts (this table) → knowledge corpus for router cost models (O5).

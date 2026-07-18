# 2026-07-17 — Linux re-baseline O0: inventory + P2P readiness sweep

Campaign: MECHNET-AUTONOMY-PLAN.html Track OXEN (O0). Read-only SSH sweep from
OMEN (`derek@100.116.82.60`), no services touched. Purpose: establish the
software baseline before the P2P microbench (O1) and placement matrix (O2/O3).

## Inventory

| item | value |
| --- | --- |
| OS | Ubuntu 26.04 LTS |
| kernel | 7.0.0-22-generic |
| GPU kernel driver | **`xe`** (modern Intel driver; not i915) |
| GPUs | 2× Intel Battlemage G31 `[8086:e223]` (the B70s) @ 0c:00.0 / 10:00.0 |
| DRM nodes | card0/card1, renderD128/renderD129 |
| Level Zero loader | libze1 / libze-dev **1.28.2-2** |
| Intel GPU L0 runtime | libze-intel-gpu1 **26.05.37020.3** (legacy 24.35 also present for OpenCL) |
| OpenCL ICD | intel-opencl-icd 26.05.37020.3 |
| llama-server (SYCL build) | present (`~/baseline/llama.cpp/build-sycl/bin/`) |
| serving | `b70-planner` active (single-card SYCL0 :8080, Qwen3-30B); facade `am4-oxen-facade` active |
| host RAM | 30 Gi total; **24 Gi used, 5.5 Gi available at sweep time** |
| swap | `/swap.img` 8 G — **100% used (8 G/8 G)**; no zram |

## Findings

1. **The Linux dividend is plausible but unmeasured.** The box runs the `xe`
   driver with a current Level Zero stack (1.28.2 / 26.05) — exactly the stack
   where cross-device (P2P) support lives. Nothing here measures it yet: that
   is O1's job.
2. **Campaign tooling is missing.** `sycl-ls`, `xpu-smi`, `xpumcli` are not on
   PATH, and `level-zero-tests` (the `ze_peer` bandwidth/latency benchmark) is
   not built. O1 prerequisite (dedicated window):
   `git clone https://github.com/oneapi-src/level-zero-tests && cmake -DGROUP_TESTS=benchmark && make ze_peer`
   (libze-dev is already installed, so headers are present). Monitoring for O2
   can fall back to `/sys/class/drm` + `free`/`smaps` watermarks if xpu-smi is
   not installed.
3. **Host-RAM pressure is live, not hypothetical.** Steady state with ONE
   planner resident: 24 Gi used, 5.5 Gi available, swap **saturated** (8/8 G).
   The 2026-07-06 OOM-kill story remains the operative constraint; O3's safe
   dual-card envelope must size swap up (candidate: 16 G NVMe swapfile vs zram
   — decision D2 in the plan) before any dual-load trial.
4. **Both render nodes are currently held** by the live `llama-server` (PID on
   renderD128 *and* renderD129 — the SYCL context enumerates both devices even
   in single0 placement) plus a python tenant on both. Consequence: (a) the
   occupancy probe's "busy" reading is honest; (b) O1/O2 runs need the
   documented eviction protocol (managed units, never kill), in a dedicated
   window.

## Next (per plan)

- **O1** (needs window): build `level-zero-tests`, run `ze_peer` 0↔1 uni/bi
  bandwidth + latency → settles "P2P active on this platform: yes/no" with
  numbers.
- **O2/O3** (needs window + D2 decision): placement matrix (single /
  dual-independent / `-sm tensor` / `-sm layer`) × context ladder with
  watermark capture; envelope doc; capacity facts → router (O5).

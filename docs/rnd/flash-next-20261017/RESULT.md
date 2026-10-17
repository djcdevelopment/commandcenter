# Flash-Next: the single-OMEN transfer study

Measured 2026-10-17, 06:36–08:35 UTC. Derek asked us to take Matt Munson’s A6000 notes at face value and test transferable ideas on the available fleet. These samples use a different quant, runtime and hardware. The result is a useful isolated research recipe; the three-dense serving configuration was restored.

## What works under the measured conditions

Two 32 GiB B70 s, Core Ultra 9 285K, 122.8 GiB OS-visible RAM; native 262,144-token allocation, one slot; attention/shared experts on GPUs, first 16 routed-expert layers’ weights on host; Q8_0 K/Q4_1 V; split30,18, batch/microbatch1024, MTP sidecar. Host weight placement alone does not establish operator execution or per-token streaming. Full launch provenance: `/home/derek/work/flash-next-lab-20261017/specs/measured-launches.json`.

| Arrangement | Actual input | Full prefill | Decode tok/s |
|---|---:|---:|---:|
| Four-GPU RPC, no MTP | 29,735 | 439.39 s | 12.57 |
| OMEN CPU16, batch 512, no MTP | 29,735 | 245.51 s | 14.26 |
| OMEN CPU16, batch 1024, no MTP | 29,735 | 158.46 s | 14.43 |
| OMEN CPU16, batch 1024, MTP | 29,735 | 163.49 s | 26.33 |
| Same | 99,735 | 728.21 s | 16.24 |
| Same | 249,735 | 2,163.90 s | 11.97 |

All answers correctly followed Cedar → crate583 →17C with the three evidence IDs, zero form repairs. This is one synthetic retrieval chain, sampled once per arrangement. Full prefill means cache_n0; disk cache was uncontrolled. The four-GPU comparison also changes topology and includes first-touch file I/O and 1GbE RPC. The paired batch arm reduced prefill35.46%; MTP increased30K decode1.825×. Short answers do not establish sustained decode throughput or broad reasoning ability. See [portable measurements](measurements.json).

## State reuse and devices

Existing MemSplice transfer code saved, copied/hash-checked and restored a 3,848-token prefix in a fresh server: cache_n3848, prompt_n25, correct answer. Tested placement and batch changes also worked. This is local Linux store-and-forward reuse; it extends the earlier cross-vendor findings without establishing a new CUDA/Linux handoff for this model.

The249,779-token target state is banked:3,128,376,420 bytes,0.882 s save,2.089 s local copy/hash. Long fresh-process restore and MTP draft synchronization are UNVERIFIED. The saved target state is not a complete speculative-execution checkpoint.

Vision passed two existing fixtures. The iGPU executed two expert layers after a per-kernel SYCL build corrected a BF16 kernel-bundle startup issue;11.07 tok/s versus 18.77 in the small host-expert restore sample gives no speed benefit. The NPU was not run; the pinned OpenVINO backend excludes this model’s IQ4_XS/IQ4_NL types. All48 host-expert layers remain untested.

The table stayed sparsely mapped:2.433 GiB of 26.82 GiB present at 249K, no table swap. The first global mincore result was invalid on the root-owned file and was explicitly corrected using owned-process page-table presence. That does not measure global Linux page-cache residency. GPU uploads released most server mappings in the all-GPU arm; CPU experts and reclaimable file cache still use RAM. No controlled Windows/Linux memory comparison was made.

## Hardware and software conditions

Host-facing links were observed at Gen4×8. Level Zero advertised no peer access either direction. Derek’s prior Gen5 recovery and Windows TP/PP/D3D12/iGPU peer-read experiments remain distinct evidence; the exact successful Windows transfer path is not recovered. A capability refusal does not by itself prove a physical motherboard prohibition. No driver, firmware, PCIe flag, ACS/IOMMU or power setting changed.

Ubuntu 26.04.1 and checked OMIX0.4.0 components match Intel’s published versions: compiler 2026.1.1, runtime 26.31.39395.13, IGC 2.40.13, LevelZero 1.32.0, XPU-SMI 2.0.1. Package policy resolves the runtime to OMIX despite a ppa1 suffix. This rules out the checked version mismatch, not every possible driver regression. Sources: [installation](https://dgpu-docs.intel.com/installation-guides/installing-omix.html), [versions](https://dgpu-docs.intel.com/overview/release-notes/OMIX/0.4.0.html).

Matt’s exact Atomic quant’s verified shard 2 contains only the 51.2B n-gram table, Q5_1,38,400,184,320payload bytes. It supports his layout description. Exact-quant inference is unrun; the model download continues onFX99. The existing quant’s lazy-table behavior works with a mixed shard in this pinned runtime, so shard-isolation requirements are runtime-specific.

## Disposition and evidence

20 Flash-Next calls; no thermal/memory guard trip. The original OMEN seats and AM4 were verified healthy/idle at 08:34:50 UTC; original timer, pause state and fence restored. No permanent serving change. Research finding `F-flashnext-fullfleet-20261017`; HEARTH `br-20261017-063648-35180850`; AMUX-84 done. Source52d42686560a9e8f441f9b9780c8890c37d2802d plus preserved patch; lab commit38f5aaa. Raw lab and saved states: `/home/derek/work/flash-next-lab-20261017`; archived small evidence: `/home/derek/work/lab-rnd/research/evidence/flashnext-20261017/`.

Read [the next-lap plan](PLAN.md), [retro](../../../SESSION-RETRO-2026-10-17.md), and [ADR-0072](../../adr/0072-flash-next-remains-an-isolated-research-recipe.md).

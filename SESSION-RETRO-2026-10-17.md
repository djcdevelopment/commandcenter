# Session retro — 2026-10-17 · Flash-Next on the available hardware

**We turned a colleague’s recipe into a measured single-OMEN research option, banked the expensive prefill, and restored the original fleet.** The surprising PCIe/peer results remain conditioned observations, with no proven cause.

## What this session was

Derek asked us to take Matt Munson’s single-A6000 notes at face value and test transferable claims, with all GPUs available and a 2–3 hour budget. The physical campaign ran 06:36–08:35 UTC. Derek then requested this documentation, plan, ADR and landing closeout. This was R&D, not production hardening or a controlled A6000 reproduction.

The first vertical slice used four GPUs over RPC. Derek’s observations about host RAM, his earlier Windows prefill, and MemSplice shifted the useful question toward one OMEN. We measured host-expert placement, batching, MTP, fresh-process prefix reuse, two vision fixtures and one iGPU placement. We inventoried the NPU boundary without claiming inference. A final249,735-token retrieval completed correctly after 36m04 s prefill at 11.97 tok/s decode. The full [result](docs/rnd/flash-next-20261017/RESULT.md) separates context allocation, occupancy, cache state and limits.

## What shipped

Lab repo `/home/derek/work/flash-next-lab-20261017`, initial commit `c862ecb` through `38f5aaa`:

| Commit | Durable slice |
|---|---|
|c862ecb, c089ffb|Pinned scope, builds, reservation and guard|
|49b6495|CUDA setup recovery and corrected table instrumentation|
|9c9d868,219d617|OMEN host placement, fresh-prefix MemSplice, iGPU build aid|
|e0dfa0b,0528c99|Vision/batching, MTP/100K, target-state banking|
|91dcc9b,f3f8d1a|Live PCIe/peer evidence and matching OMIX audit|
|c6eb924,b36b0bd,38f5aaa|Readable findings, correct249K result and verified restoration|

This closeout adds [ADR-0072](docs/adr/0072-flash-next-remains-an-isolated-research-recipe.md), [next-lap plan](docs/rnd/flash-next-20261017/PLAN.md), portable measurements, central R&D/ADR references, MemSplice’s Linux scope note, planning/memory pointers and frozen registry evidence. The old Windows records and raw measurements stay intact.

## The team retro

**Architect — Derek set the direction; Codex designed the bounded arms.** The host-expert and state-reuse direction earned a working arrangement. Using all available GPUs was an informative first slice, but1GbE and first-touch I/O made its comparison with OMEN causally weak. Next time, witness the links and cache conditions before timing, and trace operator/copy placement before attributing the bottleneck.

**Implementer — Codex built and integrated.** Isolated source/builds protected the original runtime. One CUDA link-path correction and one per-kernel SYCL build recovered concrete setup edges. The iGPU genuinely executed, but its small sample was slower; the build aid stays in the capability record. NPU availability did not justify pretending that unsupported quant types would work.

**Reviewer / QA — Codex checked substance, form and telemetry separately.** All completed retrieval chains and two vision answers were correct with zero form repairs. One synthetic chain does not establish broad reasoning ability. The invalid root-owned-file mincore result was a real instrumentation error; it was disclosed and replaced with process page-table presence. File cache and RSS remain different measures. Saved/hash-verified large state remains short of a correct fresh-process continuation.

**Operator / SRE — Derek supplied the hardware history; Codex held and restored the lease.** Three seats were stopped under an owned admission pause/fence, with temperature and memory guards. Restoration checked health and idle metrics before returning the timer, pause state and fence. No guard trip occurred. The prior Gen5 recovery and Windows peer-read experiments are useful evidence, not anecdotes to discard because today’s Level Zero capability query says0. The installed checked OMIX components match Intel’s published versions; the actual PCIe/peer cause is unresolved.

**Product / planning — Derek kept the question tied to one machine’s useful capability.** The useful result is a reusable recipe and a banked3.13 GB target state, not a claim to have reproduced Matt’s35 tok/s figure. The next cheap slice is a new-suffix restore. Production promotion, exact-quant inference, capacity and a causal OS comparison remain open. The20-call Flash-Next budget was respected; the closeout’s prose draft is a separate27B call.

## Two seats, two views

**From Codex’s seat.** I should have captured live links and peer advertisement in the opening witness. I also should have frozen the README at initial registry attachment: its later growth left one stale hash. Exact bytes were recovered from git and archived, and the reference repaired through a new registry transaction. The raw invalid measurement and historical events were preserved. Small paired controls are stronger evidence than the fastest headline number.

**From Derek’s seat — my reconstruction, not a quotation.** His learn-by-doing experiments supplied the clues the software inventory could not: a previously working Gen5 configuration, directional peer behavior, and a Windows prefill baseline. The useful collaboration is to turn those clues into bounded probes while reducing his need to narrate history repeatedly. Calling the board incapable from one API query would lose that knowledge.

## Last time’s lessons — follow-through

The latest root retro was2026-09-20; it had no pending fleet second opinion. This table grades only follow-through visible in this lap, not every intervening session.

| Earlier lesson | Status here |
|---|---|
|L-2026-09-20-1 backend scope|Acted-on: NPU backend/types read before a claim|
|2 prior-kernel faults|Pending: not sampled; no kernel fault diagnosis here|
|3 graph capture lever|Pending: no graph arm in this lap|
|4 tune on actual silicon|Acted-on: paired live batch/MTP arms; no generic-table promotion|
|5 ask driver advertisement|Acted-on, late: capability and physical-link witnesses captured|
|6 know compiler lineage|Acted-on: loaded libraries and checked OMIX versions captured|
|7 elevated Windows actions|Pending: no elevated action; Linux terminal-only rule retained|
|8 tenancy as recipe|Acted-on: ownership, readiness, idle and restoration evidence|
|9 loader quant bugs|Pending: not encountered|
|10 graph/determinism|Pending: no graph change; correct answers compared|
|11 watch process as well as log|Acted-on: PID/start-time ownership and bounded runner|
|12 promised gates must run|Acted-on:293 actual thermal/memory samples; no guard trip|

## Lessons learned

1. **L-2026-10-17-FN1:** Witness live hardware/software conditions before performance attribution. *Doc/memory:* currentGen4 and no advertised peer access coexist with earlier working Windows/Gen5 evidence.
2. **L-2026-10-17-FN2:** Saved, copied, restored and correct continuation are four distinct claims. *ADR-0072:* long-state restore and draft synchronization remain unverified.
3. **L-2026-10-17-FN3:** Check measurement permissions and semantics. *Doc:* owned-process page presence is not global file-cache residency; mincore on another owner’s file may be masked.
4. **L-2026-10-17-FN4:** Preserve a proven recipe without silently promoting it. *ADR/plan:* held-out substance and capacity evidence are still needed.
5. **L-2026-10-17-FN5:** Freeze cited mutable documents at attachment. *Practice/registry:* recover exact historical bytes, append reference corrections, never rewrite original events or measurements.

## Open loops

[The plan](docs/rnd/flash-next-20261017/PLAN.md) names each unrun slice and its evidence. Exact Atomic download continues onFX99; no new inference service. The telemetry helper remains unapplied at awaiting_review because the author’s self-verdict was refused under ADR-0069. An independent reviewer must dispose of it; documentation landing does not bypass that gate. These two operator choices are visible in [DECISIONS-PENDING](DECISIONS-PENDING.md).

## Provenance and offload scorecard

The frontier assembled facts from this conversation, lab git history and raw results, then wrote/reviewed every decision and repository change. A single unpinned `drafting` call routed to `omen-dense-27b`/`qwen3.8-27b`: job `job_7b524ea3bb7b88e31f4ad88274a397ab`, artifact `art_05956266c30a8c3364bb08037a2a96ef`,811 tokens in/615out,17.771 s. Its performance summary was useful, but it invented an iGPU target and an abandoned test, misnamed the mincore issue, and described the campaign as ending at 30K. Those statements were discarded. **edit_verdict: hallucinated**; the word-cap overrun is a separate form miss, one repair. No second-opinion fleet dispatch.

`query_offload(task_id=cx-adhoc)` returned a stale projection (watermark 2026-10-16T01:40:11Z), with no matching task row. Current offload ratio, aggregate token totals and dollar savings are unavailable; no projection writer was invoked. The direct job metadata above is the only per-call usage asserted. Lab receipt `br-20261017-063648-35180850` is done; closeout receipt `br-20261017-084106-fe1d438e` records landing validation. Research finding `F-flashnext-fullfleet-20261017` is conditional, with original events preserved.

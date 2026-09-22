# Daily local-fleet work

Derived from the OMEN-authored candidate in plan
`hearth-hermes-br-20260920-031116-00f63902-27fb0479`.
Codex corrected the command name, output-reserve wording, job limits and stop rules.
The original candidate remains in `artifacts/hermes-fx99/omen-workers/`.

1. On FX99, run `hermes-fleet fleet-status` (or
   `hermes-fleet fleet-status --json`). This is read-only and uses no inference.
   Unknown occupancy is not idle. Enter the controller with `hermes-fleet`.

2. Give Hermes a concrete deliverable, acceptance criteria, a deadline and time
   for review. Prefer one small source-packed task before expanding the workload.
   Repeated source reads without an artifact are a reason to reassess.

3. Use `cc-builder-2` and/or `cc-builder-3` through the existing conductor, with
   `omen-resident-hearth` and manual promotion. These VMs edit files and run CPU
   tools; HEARTH sends their generation to OMEN, never AM4 or cloud fallback.
   The policy permits two named builders; it does not promise eight concurrent
   Hermes builds merely because OMEN has eight native slots.

4. Keep worker prompts **plus output reserve** inside the observed 16k slot.
   Hermes and compression share AM4's separate 128k/one-slot controller model.
   Neither figure proves a single-B70 layout. Do not modify global presets.

5. When OMEN is unavailable, busy or under maintenance/tenancy, wait within the
   budget or stop. Split oversized tasks before dispatch. Never silently switch
   hardware, truncate context or treat a stale capacity record as availability.

6. Review the changed deliverable independently. Existence, an inherited test
   suite and a model's self-written retrospective are not sufficient evidence.
   Candidates stay on local fleet branches until review; no automatic main merge.
   Record model authorship separately from integration corrections.

7. Keep accepted-artifact outcomes and physical-attempt receipts for later
   routing decisions. This is evidence feedback, not model-weight training.
   Stop at the deadline even if no file exists; report the missing deliverable.

MemSplice, SYCL model rotation and 256k workers remain separate follow-ups.
DeepAgents is not newly connected here. This rollout does not certify spill-free
VRAM or deep-context performance: use the existing b70tools/native observations,
not Task Manager's shared-memory number alone, before changing the serving shape.

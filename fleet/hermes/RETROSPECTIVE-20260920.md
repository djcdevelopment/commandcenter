# Retrospective: useful local work, not just a running controller

2026-09-20 UTC. Continuation: [HANDOFF.md](HANDOFF.md).

## Outcome

We established a real, restricted Hermes-to-local-worker path: Hermes runs on
FX99, thinks through AM4's Dense 27B, and dispatches work through HEARTH to
MechNet builders using OMEN inference. Two worker candidates became useful
deliverables after review: a fleet-status formatter and an operations checklist.
One of those jobs went from Hermes dispatch to a worker file without intervention.
Controller and worker inference overlapped on different hosts.

That is narrower than the goal. We have **not** delivered an autonomous fleet
manager that chooses freely among HEARTH, DeepAgents and MechNet, rotates models,
uses MemSplice, and optimizes accepted work per unit of hardware. The current
controller still occupies both AM4 NVIDIA cards. The compact-controller attempt
did not deliver its requested worker artifact. A running service, passing tests,
and a restored machine are not substitutes for those missing outcomes.

## What actually happened

| Work | Useful result | Limit / attribution |
|---|---|---|
| Initial FX99 controller and shared-AM4 builders | Installed controller and review-first routing; capacity correction | Local coding qualification missed its artifact requirement. Codex supplied integration/corrections; test counts did not establish model coding success. |
| AM4 controller / OMEN workers, 02:41-03:24 UTC | Worker formatter and checklist; live fleet-status; 7.655 seconds of cross-host inference overlap | Formatter dispatch needed a Codex singleton-plan repair before inference. Both candidates needed review corrections. The requested delegated operating matrix failed; Codex wrote the published matrix. |
| SYCL worker-route attempt, 03:55-03:58 | Located a concrete routing refusal | The deployed worker profile admitted only the resident OMEN model. No SYCL model was launched, so this says nothing negative about SYCL throughput. |
| Compact controller, 04:01-04:22 | Loaded an existing 14B, measured single-5070 fit and short-prompt performance; exposed actual tool-call failures | No real build receipt, conductor plan, or `WORK-SHAPING.md`. The sampled CPU-offloaded shape was native 32k, not a qualified 64k controller. |

Evidence: [implementation history](../../docs/operations/HERMES-FX99-IMPLEMENTATION.md),
[rollout](AM4-OMEN-ROLLOUT.md), and [compact-controller record](RND-COMPACT-CONTROLLER-20260920.md).
All times here are UTC, not the workstation's local date.

## Where I made the work harder than it needed to be

1. **I optimized the apparatus before the output.** Setup, admission rules,
   compatibility work, and tests consumed time before a useful delegated file.
   Earlier builder attempts spent their window rereading rather than editing.
   The rollout's first useful model artifact missed its first-quarter target.
   The reference failure Derek identified on September 12 was not an abstract
   warning: the same pattern remained visible. Recovery and restoration were
   necessary, but do not turn a missed deliverable into success.

2. **I treated the resident deployment as the hardware's limit.** OMEN's eight
   16k Vulkan worker slots are a serving choice, not a B70 capability ceiling.
   Existing commandcenter benchmarks already demonstrated much stronger SYCL
   Dense performance. I should have used that evidence before discussing the
   route as though it needed to be rediscovered. The exact attribution matters:
   roughly 694-713 prompt tok/s and 19.98 decode tok/s at 119k were dual-B70
   results; 24.9 decode tok/s at 119k used the NVIDIA pair as well.

3. **I anchored on a large controller instead of total fleet output.** Moving
   the controller to AM4 freed the B70s from controller duty, but did not free
   either NVIDIA card. Derek explicitly opened the useful alternatives:
   smaller/quantized controllers, shorter work packs, CPU offload, model
   rotation, and different worker shapes. A controller's context size is not
   the objective; accepted work produced by the whole fleet is.

4. **I missed a documented startup constraint.** The installed Hermes path's
   64,000-token minimum was already noted in `hearth/etc/backends.toml`. Loading
   a 16k 14B measured fit, but its Hermes refusal was avoidable. The CLI also
   returned exit code zero on that refusal, so exit status alone was weak
   evidence. The next all-GPU 64k attempt failed KV allocation.

5. **Configured labels briefly got ahead of physical truth.** CUDA ordinal zero
   did not identify the same card as the first `nvidia-smi` row. Placement was
   corrected to the 5070 UUID before the real Hermes run. Separately, the
   CPU-offloaded candidate advertised 65536 to Hermes while native capacity
   was 32768. That let startup pass; it did not qualify a 64k configuration.
   The short completed calls were not truncated, but cannot prove long-context
   correctness. This shape must not be promoted as a successful 64k controller.

6. **Avoiding tests alone did not make the later lap disciplined R&D.** The
   compact experiment wrote no tests and ran real prompts, but still accumulated
   experiment glue, placement changes, and a slow model-transfer detour. The
   first-artifact target passed with fit observations, not the requested file.
   Several adjacent failures do not become a clean vertical slice merely by
   calling each another lap. The 8B transfer was stopped after 353 MB and remains
   an unexplained staging problem, not a model-quality result.

7. **Existing selection machinery was too easy to overlook.** The later
   documentation pass found `hearth/operator/` on the separate `operator/program`
   worktree, with proposal/validation/execution and DeepAgents/MechNet adapters.
   The deployed Hermes profile does not expose that whole surface. Those are
   different facts: missing integration is not missing implementation. The
   handoff now names the worktree and its entry contract explicitly.

## What is worth keeping

- Separation of controller and worker inference was demonstrated by physical
  calls, not just configured aliases. The reviewed formatter and checklist are
  usable artifacts, and original candidates remain available for comparison.
- Native capacity checks, output reservation, explicit backend identity and
  no silent cloud fallback made the observations interpretable. Unknown AM4
  occupancy stays unknown rather than being presented as idle.
- The 14B was actually loaded and prompted. It successfully discovered and
  queried the worker, then omitted required receipt fields, invented receipt
  IDs and failed to recover. This is a specific failure of one model/prompt/tool
  interface, not a verdict against all small controllers. There was no paired
  Dense-27B run of the same prompt establishing a comparative success rate.
- AM4 was restored to the original argv/environment, with a new process ID.
  OMEN's resident service and FX99's existing Ollama models were not replaced.
  Experiment logs preserve failures rather than relabeling them as success.

## Changes to how the next lap should run

Name one real file or accepted change before touching a model. Give the lap a
wall-clock ceiling, a latest useful dispatch time, and a delivery/restoration
reserve. Use already-present weights and existing control surfaces first.
Validate only what the real attempt needs: actual GPU UUID, actual slot context,
correct tool schema, and available output budget. Then ask for the artifact.

At the first meaningful edge, record the exact failure and stop that lap. If
setup consumes the artifact window, choose the fastest remaining route to useful
work or ask Derek at the decision point. Do not spend the delivery reserve on a
new controller, benchmark matrix, model transfer or test harness. Fixing the
winning slice comes after there is a winning slice.

Keep three labels separate: **model-generated**, **Codex-corrected**, and
**deployed**. Also separate experiment completion from controller qualification.
The compact R&D receipt is closed as done for recording/restoring the experiment;
the controller qualification failed. The rollout parent is failed against its
strict delegated-matrix criterion even though two other artifacts were useful.

## Accounting and authorship

The rollout recorded four OMEN worker calls (6,874 input / 1,841 output tokens)
and twenty AM4 controller calls (112,787 input / 4,418 output), imported once.
Input sums are not unique uncached tokens. The compact Hermes session reports
five API calls, 6,234 input / 18,857 cache-read / 1,367 output tokens; these were
not imported as canonical physical-attempt receipts. No dollar-savings or
whole-project cost claim follows from these bounded counters.

This retrospective and the handoff are Codex-authored, checked against source,
logs and read-only live observations. A short HEARTH draft used the resident
`omen-arc` model: job `job_d6e87658b526511f3519dee38e6577f8`, 4,330 input / 634
output tokens, 15.734 seconds. Its raw
[draft and metadata](../../artifacts/hermes-fx99/handoff-20260920/retro-draft.json)
are retained for attribution, **not accepted as factual evidence**. It conflated
the two missing artifacts, incorrectly preserved AM4's old PID, and misstated
restoration. Those claims were rejected rather than copied into the final account.

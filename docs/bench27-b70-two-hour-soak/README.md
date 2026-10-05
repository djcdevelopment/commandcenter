# Two-hour B70 baseline soak — offline proposal, not launched

Prepared baseline: both cards Qwen3.8-27B GPTQ INT4, MTP k2, auto (fp16) KV, 65,536 context, existing capture/prefix settings, memory 0.90, max sequences 4. No FP8 adoption is inferred. Each pair runs one sizing conversation on each card concurrently: work thinking-on with 24,000 allowance, final thinking-off with 4,096 allowance, temperature 0 and seed 42. Frozen workload source is de666ef75ce79733bffb34c5e146c9b8ddd42f30. MANIFEST.json pins exact workload, controls, reviewed drivers and harness. Byte estimates do not replace exact per-turn admission.

## Existing capability and missing piece

The reviewed paired driver runs one finite pair, emits truthful per-seat calls and terminates siblings on guard/error. The reviewed campaign driver validates frozen hashes then iterates a finite command list; it propagates signals and emits aggregate calls. The harness provides sole tenancy, fencing, preflight, restore and a maximum watchdog. **None implements a minimum measured two-hour loop.** `max_minutes=150` is a maximum, not evidence of a 120-minute load period. Choosing a fixed number of variable-duration generations cannot guarantee two measured hours.

Therefore `soak.spec.proposal.json` deliberately has `campaign: null` and status `blocked_not_runnable_duration_controller_missing`. Do not run it. `one-pair.sequence.json` is the reviewed finite unit, not a complete soak. No new code or gate change has been introduced. Prospective output paths are new and nothing has been dispatched.

## Minimal controller proposal for independent review

A small offline-reviewed controller should:

1. Verify frozen unit workload/driver hashes before launch and each iteration. Under the existing harness lease, invoke the existing campaign driver with exactly one paired command and a unique `pair-NNNN` output path. Reusing a completed directory must refuse. Keep the same arm identity as the harness spec.
2. Capture monotonic and UTC start/end timestamps per child; parse the final calls map exactly as the reviewed campaign driver does. Accumulate actual integer calls, never estimate or turn missing counts into zero. A completed pair normally contributes two calls per seat; failures retain reported partial counts. Unknown counts fail closed.
3. Start the measured load period at the first observed pair workload start, not service startup, warmup or fence acquisition. Repeatedly start the next pair immediately after both seats finish. Stop naturally after a completed pair once the measured wall interval is at least 7,200 seconds. Do not use sleep to fill time. Preserve the first pair's cold regime separately from warm timing.
4. Log every global inter-pair gap and each card's idle interval while it waits for the other card. An unbroken controller lifetime does not prove continuous GPU generation. Report per-card active span, summed active intervals, idle gaps, both-cards overlap and observed counter/thermal coverage. No zero-gap claim is allowed. If sustained load is interrupted, record the duration/gap and incomplete criterion rather than erase or fill it.
5. Propagate SIGINT/SIGTERM to the child; retain the reviewed child cancellation/drain behavior, bounded cleanup, partial counts and nonzero exit on guard/error. The external 150-minute watchdog covers the two-hour target plus final-pair grace; if the pair overruns or restoration deadline intervenes, preserve a failure rather than declare a successful soak. No silent deadline or output-budget reduction.

The controller need not generate, grade or edit reports. It should not alter the paired driver or its frozen prompts. Proposal scope is duration scheduling, truthful accounting and interval recording only. Parent decides whether to implement/review this small addition or use already-running capacity evidence satisfying the same measurement contract.

## Guard, observations and restoration

Before execution, parent must confirm sole lease, idle affected seats, no sticky thermal trip, fresh heartbeat, compatible actual argv/model/context and hashes, and enough time before restore_by and dev expiry. Start raw metrics sampling before load and stop after restoration; continue the existing guard on both cards. Record sampler gaps/error/reset segments. Use exact report start/end and harness timestamps to join thermal observations; discrete maxima do not prove continuous safety.

Both drop-ins are null: baseline is expected and must be verified, not silently installed by this spec. The harness must retain its fence, guard checks and restoration accounting. Parent supplies its existing approved HEARTH_SOURCE/HEARTH_BACKENDS environment; no stale-path fallback. A launch after 20:30 UTC would not leave the proposed 150-minute envelope before the stated 23:00 restore_by; recompute against the actual execution time rather than relaxing it. Guard/cancel/OOM/preemption/foreign-work failures stay visible. Any full-budget output risk remains unchanged by repeating it.

## Acceptance of stability evidence, not capability

Required measured evidence: at least two hours of the planned repeated campaign with both cards participating throughout, explicit per-card/overlap/idle-gap accounting, complete available counter and guard records, truthful calls and no hidden foreign requests, and verified restore/released tenancy. The parent must evaluate gaps rather than treating two distant timestamps as two hours of continuous load. This is stability evidence only: no report acceptance, no qualified long output, no two-slot capacity, no review-minute economics and no new held-out rate.

A capacity-drain campaign can replace this dedicated soak only if its actual records establish the same two-hour two-card load, recipe, guard, gaps and restoration conditions. No replacement is claimed in advance. Keep capacity and soak interpretations distinct in final records.

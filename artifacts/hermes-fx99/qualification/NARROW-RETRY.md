# Authorized narrow retry — 2026-09-20

Useful artifact: an independently local-model-authored native capacity helper,
stdlib tests, and a short review, compared against the existing Codex helper.
One paired retry only, existing AM4 27B endpoint, manual promotion, no B70 use.

UTC window: 01:43:46–01:51:46 (eight minutes).
Latest useful dispatch start: 01:45:00; first written artifact gate: 01:45:46.
Generation cutoff: 01:49:46. Delivery/review/restoration reserve: final two minutes.
No additional retry is authorized by this window. If the artifact gate is missed,
stop the weak path and preserve its evidence. Default builders and main stay unchanged.

Previous implementation remains Codex-authored; no local credit by association.

## Outcome

Both builders delivered all three real artifacts. Builder 3 finished in 202.49s,
builder 2 in 229.754s (01:49:35 UTC), before the generation cutoff. Hermes itself
created and dispatched receipt br-20260920-014532-19828821; plan
hearth-hermes-br-20260920-014532-19828821-2115d229. The two-minute artifact gate
was missed during controller dispatch; Derek explicitly approved using the
remaining window. Both first actions wrote helpers, observed by 01:47:03.

This is useful delegated delivery, **not unassisted correctness qualification**.
Both helpers pass 19/21 independent contract probes. Both reject identical
duplicate rows and let an unrelated unready alias veto the selected endpoint.
Their original outputs are preserved unchanged under narrow-retry/cc-builder-*.
Builder 2 initially overrode unittest.TestCase.run, so no tests ran; it corrected
that itself. Final independent reruns: builder 2 has 10 passing tests, builder 3
has 11. Their tests omit the two cases found in review. Builder 3's report also
misstates timestamp coverage: its tests use age +60s and -31s, not the boundaries.

## Integration and attribution

- Local model: two helpers, two test suites, two short reports. No repository
  exploration; no GPU or lifecycle actions by either worker.
- Codex: independent 21-case review, integration of builder 3's 11 tests (only
  import and provenance header changed), and two additional regression cases.
- The existing Codex helper was retained; neither weaker generated helper was
  substituted into production. New capacity suite: 35 passed. Receipt/policy/
  controller regressions: 60 passed, 11 subtests. Total this review: 95 passed
  plus 11 subtests, separate from the model's standalone test reruns.

## Accounting and final state

17 physical local AM4 requests: Hermes 5, builder 2 seven, builder 3 five.
Recorded usage: 64,402 input and 6,665 output tokens; no dollar estimate invented.
Imported through ExecutionLedger; all 17 replays were duplicate-safe. Physical
receipts still lack framework-run correlation, explicitly recorded rather than
reconstructed. No further inference was dispatched.

Manual review held both candidates: 6d90228d93f72ffd42c4eb4f537b21b86efdb2f6
and a0be0d30ea66602512ae17c0ec8d9e100ef4c9cc. Farmer main remained
303a2f0f7c5cedf192e1f8642d7984822415d742; both default runner hashes match the
previous baseline. No external harvest/push, automatic promotion, model swap,
B70 use, or service change was made. Passive final readiness: native AM4 128k,
one slot, GPU placement explicitly unknown. Both workers exited normally.

Raw evidence, model reports, review probes and accounting: narrow-retry/.
Recommendation: keep this as a reviewed small-task lane; do not call either
worker generally coding-qualified or expand into a new benchmark window.

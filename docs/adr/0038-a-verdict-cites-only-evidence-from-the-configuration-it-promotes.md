# 0038 — A verdict cites only evidence measured on the configuration it promotes

**Status:** Accepted (2026-08-27); enforced as a promotion gate the same day

**Companion to:** `docs/adr#0034` (the dual-B70 rung is the door default),
`docs/adr#0031` (a pin chooses a rung, not a waiver of arithmetic)

## Context

The Qwen 3.8 campaign was built to decide one thing: whether a dense 27B candidate should
replace the Qwen3-30B-A3B MoE as `omen-arc`, the door's default rung. It measures
throughput, latency, deterministic quality, blinded pairwise quality against both the
incumbent and a pinned frontier reference, a 60-minute soak, and thermal and memory safety
— then applies fifteen gates and emits a verdict.

On 2026-08-27 the campaign ran to completion and the machinery did something quietly wrong
before any human read the result.

`choose-topology` selected `qwen27-dual-production` **with multi-token prediction (MTP)
enabled** — speculative decoding roughly tripled throughput, from 510 to 1591 jobs/hour, so
it was correctly the fastest fully-valid production-shaped configuration. Meanwhile every
deterministic assay row feeding the scorecard had been measured with **MTP off**. The
scorecard was about to stamp a candidate id ending `-mtp-on` with a pass rate, a family
breakdown, and a blind win rate computed entirely from MTP-off text.

That would be defensible if the two paths were equivalent, and speculative decoding is
*supposed* to be exactly that: drafted tokens are accepted only when they match what the
target model would have produced, so at `temperature: 0` the output should be identical.

It was not. Comparing response text between MTP-on and MTP-off legs at matched
client/round/prompt, at temperature 0:

- **Zero identical responses** across every cell compared (p512-c1, p512-c4, p8192-c1,
  p8192-c4).
- The divergence is **systematic, not tie-breaking noise**: all **126** requests across five
  `p8192` MTP-off cells were invalidated as `short_generation` — the model stopped early,
  117 tokens against a 200-token budget — while MTP-on at the same cells generated full
  length. Batch-dependent floating-point nondeterminism explains *some* divergence in a
  correct implementation; it does not explain a clean 100%-versus-0% split.

The campaign's own MTP compatibility gate (stage 02) had passed this configuration. It
compared **validity rates** between the two paths — 0.875 against 0.875 — and concluded
they were comparable. Equal scores on a rubric are not equivalence, and the gate never
compared an output to an output.

A related defect made the mixture worse rather than merely unproven: `_deterministic_quality`
resolves duplicate task rows by first-seen order over a filesystem glob, so with assay rows
present from both regimes the headline pass rate was computed from an arbitrary blend of the
two.

## Decision

**A promotion verdict may cite only evidence measured on the exact configuration it
promotes. Evidence from a neighbouring configuration is not weaker evidence — it is no
evidence, and absent evidence counts as unproved.**

Concretely, in `campaign/qwen38/qwen38_campaign.py`:

1. `compile_scorecard` records a `candidate.quality_evidence` block naming the MTP regimes
   the deterministic quality rows actually came from, the winner's regime, and whether they
   match.
2. `evaluate_promotion` gates on it (`quality_measured_on_winning_config`). A missing block
   evaluates to `false`, so silence never reads as success.
3. The golden fixture and two tests pin the behaviour: a regime mismatch blocks promotion,
   and so does absent evidence.

This generalises past MTP. The same rule applies to any axis the winner is selected along
— quantization, slot depth, split mode, thinking regime — because the selection step is
free to pick a configuration the quality step never exercised.

The rule deliberately does **not** try to decide whether the MTP divergence is an engine
defect or acceptable nondeterminism. That question stays open (see `DECISIONS-PENDING.md`);
the gate only refuses to let a verdict depend on the answer without knowing it.

## Consequences

- **Today's verdict is unaffected in outcome.** `do_not_promote` was already determined by
  throughput at 0.687× against a 1.25× floor. The new gate is the third of three failures,
  not the deciding one — which is the cheapest possible moment to add a gate.
- **A future candidate cannot be promoted on quality it never demonstrated.** That is the
  whole point; the failure mode was live and undetected until output text was compared
  directly.
- **The campaign now costs more to pass.** Any run whose winner uses a non-default axis must
  measure quality on that axis too, which is an extra assay leg inside the outage window.
  That cost is correct: it is the cost of the claim being true.
- **Stage 02's MTP gate needs strengthening separately.** It should compare outputs, not
  validity rates. Left as an open decision rather than changed mid-campaign.

## Related finding, recorded here because it bounds the same decision

The campaign also established that this rig's ceiling on placement and depth is **cooling on
one card**, not capacity. Both thermal aborts — the replica-per-card placement at a light
cell, and the 128K context tier — reached 96 °C on VRAM of the adapter at PCI
`0000:04:00.0` while its sibling sat at 86 °C, GPU cores idled at 77–79 °C, commit headroom
held 56.7 GB and shared memory 1.2 GB. At the production operating point the same model
sustained a 60-minute 16-client soak at 82 °C with zero system events. Any future campaign
that wants those placements back should treat VRAM cooling on that specific card as the
thing to fix.

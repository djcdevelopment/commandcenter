# 0068 — The Qwen3.8-27B serves its vision tower on demand (seat 1 swap-in), resident on seat 0 only if traffic earns it

**Status:** Accepted (Derek, 2026-10-15: "done, that makes sense", on the amended recommendation below). Drafted by Claude Fable 5.1, session cc-0ad7aa53. Amendment at acceptance: the on-demand swap onto seat 1 is the default; the resident seat-0 switch is the reserve, taken only when a week of door traffic shows image questions often enough that the swaps become a nuisance, and only with seat 0's vision-recipe throughput measured first. Nothing in ADR-0053 or ADR-0066 changes.

## Context

Daily capability 5 (selective perception) has been operational through CPU OCR plus deterministic math and paused
on one question: whether the 27B's own vision tower should serve image questions, which needed a resident seat
with `--language-model-only` off. The lever ledger carried "Native vision on the Qwen3.8-27B" as planned since
2026-08-26 and never run; the only prior vision numbers were Windows readings (6/8 charts, 7/8 screenshots, 6.8 tok/s).

On 2026-10-15 the question was measured on Linux through the fenced-arm harness (`fleet/experiment_linux.py`) on
seat 1, the two-dense recipe with `OMEN_LANGUAGE_MODEL_ONLY=0`, `OMEN_MTP_K=1`, `OMEN_MAX_NUM_SEQS=2`
(`docs/rnd-log.md` rows 2026-10-15 "item 15" and "item 15b"):

- the four frozen perception fixtures (`lab-rnd/research/evidence/daily-capability-20261001/selective-perception`):
  10 of 10, including the refusal on the unreadable image, median 0.5 s per answer;
- a fresh repeat: 10 of 10, identical answers;
- a variation set (`lab-rnd/research/evidence/vision-variation-20261015`: two real screenshots, one synthetic chart
  drawn from measured numbers and documented as synthetic, one degraded copy): 14 of 14, including a count, a
  two-value comparison, a difference, a ratio to one decimal, and the refusal, median 0.4 s.

That is the workstream's own acceptance line (correct on supported fixtures, fresh repeat, meaningful variation,
explicit refusal on ambiguous input), met on the model's native tower with no OCR pipeline in the loop.

## Decision

1. Default: vision is served on demand. The vision recipe (the two-dense 27B with `--language-model-only` removed,
   MTP `k=1`, `max-num-seqs 2`, 65,536 context, prefix caching and the XPU decode graphs) is swapped onto seat 1
   through the fenced harness (`fleet/experiment_linux.py`, drop-in `zz-lv15-vision.conf`), exactly as it was
   measured: swap-in 96–118 s, restore 34 s, hashes verified. Seat 0 keeps the full text recipe (MTP k=2, 4
   sequences) at all times; seat 1 keeps it whenever no image session is open.
2. Routing: `daily/perception.py` keeps its contract (CPU OCR first, deterministic math where numbers are
   extracted, `needs_vision` only where text cannot establish the answer). A `needs_vision` disposition reaches the
   vision recipe through the door (`task_family="perception"` or the existing vision tag), which requests the swap
   and releases it; callers never name a seat (lab intent). The router's capability record carries `vision:
   on_demand` for `omen-dense-27b-b`.
3. Reserve: the resident switch on seat 0 (the recipe above applied by `omen-profile switch` with its snapshot,
   hash verification and a `CLAUDE-APPROVED.md` receipt naming this ADR). Taken only after (a) a week of door traffic
   shows image questions often enough that the swaps are a nuisance, and (b) seat 0's decode and concurrency under
   the vision recipe are measured and recorded. Choosing it is a dated note on this ADR, not a new ADR.
4. Nothing moves before the three cap nights (10-15, 10-16, 10-17) end; swaps happen in daytime dev windows.

## Consequences

- Image questions get a local answer at sub-second latency with refusal on unreadable input, which closes the pause
  on daily capability 5; the CPU path remains the zero-VRAM fallback under any profile.
- The cost of vision is about two minutes of swap time per image session on seat 1, measured, instead of an
  unmeasured permanent tax (MTP k=1, two sequences) on the seat that carries most of the lab's text work.
- Night work on three-dense is unaffected; the swap is never taken while a night window is open.
- Open: the capability record field and the door's perception routing (swap request, release, queueing of a second
  image session) are code changes in the flash repo that follow this acceptance; vision at length (a 20-image batch,
  decode rate) and MTP k=1 vs k=2 under the vision recipe are unsampled; the resident-switch throughput number is
  the precondition for decision 3.

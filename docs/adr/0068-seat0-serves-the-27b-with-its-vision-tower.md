# 0068 — Seat 0 serves the Qwen3.8-27B with its vision tower

**Status:** Proposed (drafted by Claude Fable 5.1, session cc-0ad7aa53, 2026-10-15, on Derek's decision "all those seem reasonable, i agree" to the recommendation that seat 0 moves to the vision recipe after a repeat-and-variation pass). Acceptance is Derek's. Nothing in ADR-0053 or ADR-0066 changes.

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

1. Seat 0's resident recipe becomes the vision variant of the two-dense 27B: the same weights
   (`/home/derek/models/qwen3.8-27b-gptq-int4-mtp`), `--language-model-only` removed, MTP `k=1`, `max-num-seqs 2`,
   65,536 context, prefix caching and the XPU decode graphs as today. It is applied the way every seat change is
   applied: a staged drop-in under `omen-vllm@0.service.d`, `omen-profile switch` with its snapshot and hash
   verification, and a receipt in `CLAUDE-APPROVED.md` naming this ADR.
2. Seat 1 keeps the language-only two-dense recipe (MTP k=2, 4 sequences) so the pool keeps one full-speed text
   replica. The router's capability record for `omen-dense-27b` gains `vision: true`; `omen-dense-27b-b` stays
   `vision: false`.
3. `daily/perception.py` keeps its contract: CPU OCR first, deterministic math where numbers are extracted, and
   `needs_vision` only where text cannot establish the answer. A `needs_vision` disposition is routed to the vision
   seat through the door (`task_family="perception"` or the existing vision tag), never to a seat by name from the
   caller (lab intent: callers never choose seats).
4. Reserve option, if the cost in (5) proves too high under door traffic: seat 0 stays language-only and the vision
   recipe is swapped onto seat 1 on demand through the fenced harness, exactly as it was measured. Choosing the
   reserve is a note on this ADR, not a new ADR.

## Consequences

- Image questions get a local answer at sub-second latency with refusal on unreadable input, which closes the pause
  on daily capability 5; the CPU path remains the zero-VRAM fallback under any profile.
- Seat 0 gives up MTP k=2 and two of its four sequences. On the 2026-10-15 engine A/B the language-only seat decodes
  at 47 to 56 tok/s with k=2; the vision recipe's decode and concurrency under door load are not yet measured and are
  the first thing to record after the switch (the rnd-log "not sampled" list for items 15 and 15b).
- Night work on three-dense is unaffected tonight: the switch is applied in a daytime dev window after the three
  cap nights (10-15, 10-16, 10-17), not before, so the capacity measurement keeps one recipe on both replicas.
- Open: the capability record field and the door's perception routing are code changes in the flash repo
  (`knowledge/`, the router's catalog) that follow acceptance; the 24-task sweep named in the ledger is subsumed by
  the 10 + 10 + 14 here, and a larger batch at length is still unsampled.

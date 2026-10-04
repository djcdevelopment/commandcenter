# 0060 — The dense seat runs native Flash Attention with MTP k=2

**Status:** Accepted (2026-10-04, omen-linux). Derek accepted it by approving the plan that names it
(`~/.claude/plans/staged-soaring-piglet.md`, "Approving this plan also decides"). Live since the seat restart at
2026-10-04T01:38Z.

**Companion to:** ADR-0059 (the dense 27B is served for multi-step work), ADR-0048 (candidates and verdicts),
`docs/sizing-map.md`. Evidence: `~/work/lab-rnd/research/evidence/qwen38-thinking-pilot-20261003/RESULT.md` (findings
`F-qwen38-thinking-pilot-20261003`, `F-qwen38-flash-attention-20261003`), Codex's lever inventory
`~/work/lab-rnd/research/evidence/qwen38-levers-20261003/`.

## Context

Seat 0 (Qwen3.8-27B GPTQ-Int4 with an MTP draft head, one Arc Pro B70, 65,536 window) was launched with
`--attention-backend TRITON_ATTN` hard-set in `start-vllm-seat.sh` and one MTP draft token. Every speed figure in the
lab's records about the 27B ("about 10 tokens a second", "prefills slowly on the B70", deadlines of 1,800 and 2,400 s)
was measured on that recipe. On 2026-10-03, with the door able to run a thinking turn (`inference.deliberate`), the same
frozen tasks were run one setting at a time under `fleet/experiment_linux.py`:

| | Triton, k=1 | Flash, k=1 | Flash, k=2 |
|---|---|---|---|
| Prompt read, 14.6K / 24.5K / 38.9K tokens | 95 / 263 / 661 s | 8.6 / 15 / 26 s | the same |
| Decode at those depths | 16.3 / 12.2 / 9.1 tok/s | 48.5 / 47 / 46 tok/s | 58 / 55 / 55 tok/s |
| Sizing task, both turns | 1,154 to 1,292 s | 202 to 264 s | 252 s |
| Backoff task, both turns (both reports pass every criterion) | 712 s | 163 s | 130 s (not graded) |

Same answers: planted lines returned exactly 36 of 36 on Flash (12 of 12 on Triton); exact quotes attached 46 of 46
(Triton 47 of 47); four stored requests replayed three times each gave byte-identical answers on both backends in 11 of
12; one whole task (5,834 reasoning tokens, the answer, the delivery answer) is byte-identical between a Triton run and a
Flash run. The KV pool is the same size on both (95,783 to 99,793 tokens across starts; 76,706 on some starts of either).

## Decision

1. Seat 0's resident recipe is `OMEN_ATTN_BACKEND=FLASH_ATTN` and `OMEN_MTP_K=2`, set in the 27B's own recipe file
   `omen-vllm@0.service.d/stage2-27b-mtp.conf` (not in a second drop-in: one drop-in per key).
2. The recipe is declared on the rung (`flash_attention = true`, `speculative = "mtp-k2"` on `omen-dense-27b` in
   `backends-linux.toml`). Those two keys are part of the serving profile a capability record carries, so records made on
   the old and the new recipe are different configurations. `sizing_map --check` fails when the declaration and the seat
   disagree (`declared-recipe-is-the-seat`), and the map now has a row for the attention backend.
3. Other models that load on top of the 27B's recipe file (the staged `stage4` to `stage7` drop-ins) clear
   `OMEN_ATTN_BACKEND` and so keep the launcher's default. The vision recipe (`stage8-27b-vision`) replaces the file and
   stays on Triton with k=1 until Flash is tested with vision input.
4. The old recipe stays reproducible as an experiment arm: `zz-b0-triton-k1.conf.staged`.
5. Deadlines and ceilings (`work.produce` 16,384 tokens and 2,400 s; local-work default 1,800 s) are not changed: they
   are bounds, and are now generous.

## Added 2026-10-04: the soak, and seat 1

**The soak passed** (`~/work/lab-rnd/research/evidence/seat0-flash-adoption-20261004/RESULT.md`): 53 minutes of continuous
27B work through the door with every other seat busy; no restart, error or abort; 2,217 of 2,219 comparable judge verdicts
agree with the old recipe's; both accepted deliveries re-submitted came back byte for byte; needles exact at 54K tokens.
Short judge calls gain little (about 2,900 items an hour against roughly 1,250 to 2,400): the gain is in long contexts.
The card's VRAM sensor reads about 99 °C under sustained load on either recipe (critical 105 °C), at its 230 W cap; it
touched 104 °C with three and four conversations at once.

**Seat 1 (the 30B) runs Flash Attention too**, since the seat restart at 2026-10-04T04:07Z. The approved plan made this
conditional on a second paired probe showing identical answers and no slower burst; it showed a 26K-token prompt read in
5.8 s against 16.8 s, decode 81 against 65 tok/s at that depth, an eight-call burst the prefix cache could not serve in
17.5 s against 37.6 s, and byte-identical needle and copy answers on both pairs
(`~/work/lab-rnd/research/evidence/seat1-flash-pilot-20261003/RESULT.md`). It is set in its own drop-in
`omen-vllm@1.service.d/attn-backend.conf` and declared on the rung (`flash_attention = true` on `omen-vllm`). Roll back:
remove that file and the rung line (tracked and deployed), `daemon-reload`, restart `omen-vllm@1` at zero leases.

## Rollback

In `stage2-27b-mtp.conf` (tracked and deployed): `OMEN_MTP_K=1`, remove the `OMEN_ATTN_BACKEND` line; in
`backends-linux.toml` (tracked and deployed) remove `flash_attention` and `speculative`; `systemctl --user daemon-reload`;
restart `omen-vllm@0` at zero leases; `~/bin/wait-vllm-seat.sh 0`; `host_config --check`, `sizing_map --check`.

## Not covered by the evidence (listed, not answered)

- Sustained load: the soak under real door traffic is recorded in `docs/rnd-log.md` (2026-10-04) and decides whether
  this record stands; a failed soak rolls back.
- Vision input, tool calls, more than two conversations at once, mixed-depth batches beyond one pair.
- One replayed request gave a different answer on Flash when the prompt was read fresh than when the prefix cache
  served it; why is not known.
- Seat 1 (the 30B) gains less from the same change (prompt read 2 to 3 times faster, decode about 25% faster at 26K
  depth, eleven of eleven answers identical; one sample per arm). It is not part of this record.

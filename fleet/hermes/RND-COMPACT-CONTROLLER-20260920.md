# Compact-controller R&D: actual model loads and a real Hermes prompt

2026-09-20 UTC. Useful target: a Hermes-created/dispatched `WORK-SHAPING.md`
through the existing OMEN worker lane, while freeing AM4's 4070 Ti. No new
test files, test runs, cloud inference, or production framework changes.

Start 04:01:32; first-artifact target 04:07:30; generation cutoff 04:17:06;
delivery/restoration ceiling 04:25:06. The first useful observations were fit
and startup refusals, not the requested delegated artifact. Final Hermes run
ended around 04:17:00; AM4 restored and fleet status ready by 04:18:57.

## Results

| Lap | Loaded/requested shape | Actual observation | Result |
| --- | --- | --- | --- |
| 1 | Existing Qwen2.5-14B Q4_K_M, all GPU, 16384 context, Q8 K/V | RTX 5070 10162/12227 MiB; RTX 4070 Ti 15/12282 MiB | Native ready. Hermes rejects contexts below 64000 before inference. CLI nevertheless exits 0. No quality sample. |
| 2 | Same weights, all GPU, 65536 context, Q4_0 K/V | Native CUDA allocation of 3456 MiB KV fails | Not resident; Hermes connection errors are not model-quality evidence. No answer. |
| Staging only | Existing Llama3.1-8B Q4_K_M on FX99, native model context reported 131072 | Copy via Windows made only 353009664 bytes before cancellation | NOT loaded or sampled. Partial destination removed; original FX99 weights retained. Transfer slowness is unexplained. |
| 3 | Same 14B, `-ngl 40`, CPU + 5070, requested 65536/Q4 K/V | Native caps actual slot to 32768 training context. RTX 5070 10188 MiB; 4070 Ti 15 MiB. Real first request: 4008 prompt tokens, 1124.07 prompt tok/s, 17.72 decode tok/s; later decode roughly 16–17.5 | Real Hermes discovery and `query_omen_worker` succeeded. Receipt creation/dispatch failed. No worker artifact. |

The candidate was initially placed on the 4070 Ti because CUDA ordinal zero
did not match nvidia-smi's ordering. Before Hermes ran, placement was corrected
to the RTX 5070 UUID `GPU-a1f65cc0-44d9-7854-6785-7d93e686da2f`.

**This is not a valid 64k Hermes configuration.** In lap 3 the isolated Hermes
configuration advertised 65536, while native `/props` and `/slots` reported
32768. That let startup pass; it does not establish 64k capacity. Actual prompt
traffic remained short and native completed requests reported `truncated = 0`.
The installed production Hermes profile was never edited. Do not promote this
configuration or infer long-context correctness from these calls.

## The observed controller-quality edge

Original messages are in
`artifacts/hermes-fx99/compact-controller/sessions.json` (not a model-written
self-report). The 14B discovered and called `query_omen_worker` correctly.
It then omitted the required `request` argument, combined unsupported local
tool batches, placed top-level fields inside a JSON string, and invented
receipt IDs before any receipt existed. Subsequent errors did not repair the
sequence. It also attempted a file read despite the prompt's no-read instruction.

The outer 100-second limit ended the run after 102.023 seconds including
interrupt cleanup. No real build receipt, conductor plan, or `WORK-SHAPING.md`
was produced. Five model API calls are reported by Hermes; session-reported
usage is 6234 input tokens, 18857 cache-read tokens and 1367 output tokens.
These counters are not imported as canonical HEARTH physical-attempt receipts.
The run used a private SSH-forwarded native endpoint, not the production facade.

This is one failure on this prompt/interface with an old 14B model, not proof
that all small controllers fail. CPU offload made the short request fast enough
to try; speed did not translate into successful orchestration here.

## Exact recorded slice and replay boundary

Source: `rnd-am4-seat.py`, `rnd-14b-hermes.py`, `rnd-14b-task.md` in this directory.
AM4 keeps the original argv/environment in an owner-only snapshot under
`/home/derek/.local/state/hermes-rnd-20260920/resident.json`. Do not copy that
private snapshot into git. The scripts are operator-run experiment helpers,
not a lifecycle service or accepted production preset.

With the lap's private native key staged, an idle resident, and the recorded
snapshot intact, the CPU-offload serving command used was:

```text
ssh am4 python3 /home/derek/work/am4-dual-nvidia-poc/rnd-am4-seat.py 14b-cpu
```

The two temporary forwards were Windows -> AM4 native and FX99 -> Windows:

```text
ssh -N -L 127.0.0.1:18091:127.0.0.1:18090 am4
ssh -N -R 127.0.0.1:18091:127.0.0.1:18091 fx99
```

The exact real-agent invocation (existing pinned Hermes, not a simulated loop):

```text
ssh fx99 /home/derek/.local/share/hermes-fleet/venv/bin/python /home/derek/.local/share/hermes-fleet/rnd-14b-hermes.py rnd-14b-5070-cpu-64k 65536 rnd-qwen25-14b-q4 100
```

That label identifies preserved evidence; use a new label if repeating so it
is not overwritten. Staging used existing controller credentials over SSH;
no credentials are in these sources. The AM4 temporary `native.key` was removed
after restoration, so these commands deliberately do not constitute an
unattended replay. Any new lap needs a fresh idle check and private key staging.

Restoration used:

```text
ssh am4 python3 /home/derek/work/am4-dual-nvidia-poc/rnd-am4-seat.py restore
ssh fx99 /home/derek/.local/bin/hermes-fleet fleet-status
```

Original native argv/environment restored; new PID 2151074, original Dense27B,
131072 context, one idle physical slot. Both lap SSH forwards stopped. OMEN
remained the original MoE worker service: 16384 context, eight slots, zero busy.
FX99's existing Ollama residents were not changed. No model downloads completed;
only the incomplete new 8B destination copy was removed.

## Not sampled

- Llama3.1-8B or any other smaller model as the controller.
- Tool-call behavior with a simpler prompt or narrower tool exposure.
- Genuine 64k context on the 14B, or a supported smaller-context Hermes setting.
- Concurrent useful work on the freed 4070 Ti, MemSplice, or B70 model rotation.
- Automatic framework selection among HEARTH, DeepAgents, and MechNet.

Receipt: `br-20260920-040805-4b481cc7`. The earlier SYCL-worker route lap ended
separately at the pinned-model refusal (`br-20260920-035537-21f37ea1`). Its
edge-log draft was the existing B70 MoE's work, invoked by Codex through HEARTH;
this report and the experiment glue are Codex work. No delegated source report
was silently replaced by this account.

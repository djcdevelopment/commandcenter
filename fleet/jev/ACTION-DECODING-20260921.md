# Valid model output lost by the worker parser

The corrected JSON-action decoder is deployed on **cc-builder-2 only**. It uses
the standard JSON decoder instead of counting braces inside quoted strings.
The unchanged worker action executor wrote two exact Python files from responses
that the previous parser rejected. No model, route, context, output allowance,
default runner, other worker or gateway process changed.

This is a Codex correction with a failed local-model candidate, not an unassisted
worker patch. The real JEV / MechNet / Hermes loop ran once, with no new test files
or second builder attempt.

## Actual build and saved-response recovery

JEV selected `jev-37a75c99bc3d31573aba4837` at11:14:19 UTC: fit2.99,
confidence0.99, ambiguity0.16, gates unchanged. The worker finished five turns
in41 seconds; candidate commit `253d50ee729188f4fb437be933ce31f715c48d95`.

| Response | Observed result | Offline replay with the correction |
| --- | --- | --- |
| First,836 output tokens /13.282s | Valid JSON discarded; its2,773-byte candidate preserved the transport function | Decoded exactly; source still fails prose-suffix handling and contains an upstream-redacted CLI option |
| Second,467 tokens /7.469s | Invalid JSON discarded | Still refused; no invented repair |
| Third,472 tokens /7.859s | Valid JSON discarded; source had already lost the transport function | Decoded, but code remains unusable |
| Fourth,400 tokens /6.985s | Shortened file finally written | Missing transport and missing `re` import; candidate rejected |
| Fifth,192 tokens /3.891s | `finish`, no syntax-check action | Completion is not acceptance |

The exact first and third saved responses now decode. Recovered sources were
**not written by the original worker**. Evidence contains hashes and recovered
code, not private reasoning fields. This sequence proves lost valid output; it
does not prove the counterfactual run would have passed or establish a speedup.
This defect is separate from cycle13's proven2,048-token truncation.

The immutable request also proves `--task-id` was changed before inference,
while `chat()` was supplied intact. The separate redaction-policy proposal
remains source-only; no historical request or live guard was changed.

[Raw candidate, decision, review and trace](evidence/20260921-action-parser-candidate.json).
[Exact saved-response replay and first recovered source](evidence/20260921-action-parser-recovered.json).

## Independent acceptance and deployment

Codex retained the original adapter and added the parser/hook. Its first direct
version failed array-wrapped-action handling; that failure is retained. The
correction passes17 focused checks: quoted braces, escapes, nested values, fence
precedence, prose, malformed/truncated responses, container rejection, syntax and
unchanged transport AST/CLI option. It fails closed on incomplete JSON-looking
containers, without salvaging nested commands. This is not exhaustive proof.

Only `scripts/agent_hearth.py` on cc-builder-2 was installed, using an exact old
hash guard and `.jev-json-parser-20260921.backup`. Generic runner and default
configuration hashes are unchanged. New adapter SHA256:
`9c3dec010663bd52b8f0b3352424a258d0982bfeb6422664c61d7aa3012a66d1`.
New HEARTH runs load it without restarting the gateway or worker service.
[Checks, deployment hashes and real file-write replay](evidence/20260921-action-parser-execution.json).

## Review, timing and accounting

Hermes delivered its report in45.281s and released AM4. It correctly found the
missing import/transport but made incorrect fence/scanning claims and treated an
authoritative capture as ambiguous. Its mixed INCONCLUSIVE/NEEDS_WORK verdict is
advisory. Independent evidence was attached after review started; the pending
gateway evidence gate was neither claimed live nor bypassed.

Window11:13–11:33 UTC; first-file target11:18, generation ceiling11:28, final five
minutes reserved for delivery. The unusable file was observed11:15:00. The final
direct correction was saved before11:18; checks completed11:18:37,38 seconds after
that target. Installed file-write replay completed11:19:35. No extra generation.

JEV:958 input tokens, estimated USD0.000040236. Cumulative14 attempts:
USD0.000458808 known-usage estimates plus USD0.005505024 uncertain reservations;
USD0.005963832 booked against the unchanged USD0.10 cap. Codex effort, hardware
and power are not included. AM4 owner null/model absent at11:20; no KV reuse.

# Why the local worker produced no source

Initial diagnosis captured 2026-09-21 10:34 UTC; source-backed, not a model report.

For plan `hearth-hermes-br-20260921-102014-e72849c0-33f9fc2f`, the actual worker
log shows three OMEN generations at exactly 2,048 output tokens. Each was
followed by `no JSON action parsed; nudging`. No action reached `do_action`, so
no `write_file` ran. With 23 seconds left (below its 25-second step reserve),
the loop stopped. Its finalizer created `retro.md` and returned exit0; that is
why the farm reported a committed build despite no requested source artifact.

| Attempt | Input tokens | Output tokens | Generation time |
| --- | ---: | ---: | ---: |
| 1 | 3,149 | 2,048 | 40.781 s |
| 2 | 3,421 | 2,048 | 41.484 s |
| 3 | 3,695 | 2,048 | 42.797 s |

Sources: installed worker `scripts/agent_hearth.py:11` defaults to2,048 output
tokens and passes that value to Hearth. `agent_openai.py:248` retries unparseable
responses; lines226–231 enforce the remaining-time reserve; lines163–183 create
the fallback retrospective and line269 returns0. The count of four steps in the
retro includes the final budget check: there were only three model generations.
The worker reached its response limit, not its16k input-context capacity.

## Confirmed output-cap failure

Integrity-checked saved replies resolve the ambiguity: all three begin a
`write_file` action for `operations_page.py`, but the `content` JSON string never
closes. Python's standard JSON decoder reports an unterminated string starting
at offsets415,376 and395 respectively. Result sizes are8,390/8,384/8,351 bytes.
This is not the custom action parser losing a complete JSON response: there is
no complete response for even the standard decoder to parse. No malformed or
partially repaired action was executed.

[Response inspection](evidence/20260921-worker-response-inspection.json) retains
artifact IDs, hashes, sizes and parse outcomes, not thought fields or response
prose. The corresponding input prompts were3,149–3,695 tokens, well below the
16k native slot limit; the hard2,048-output allowance was the immediate limit.

## Bounded correction, not yet live-inference qualified

Codex changed only the OMEN transport's default allowance to**4,096 tokens** and
added `requested_max_tokens` to its attempt log. The existing gateway already
admits at most4,096 output tokens and independently checks total native context;
neither guard was relaxed. Explicit caller-supplied limits still pass through.
No JSON repair, parser replacement, new retry loop or wall-budget extension.

The adapter was installed on **cc-builder-2 only** at10:37:40 UTC after checking
the existing file hash and no active pilot jobs. Source is versioned at
`fleet/hermes/remote/worker/agent_hearth.py`; cc-builder-3 was not redeployed.
Default `runner.json` hash is unchanged. No gateway/worker restart or model
operation was needed; new worker processes load the updated script.
Backup: `agent_hearth.py.jev-output-limit-20260921.backup`.
Installed SHA256: `f664a3967c4ce42d32d2c9be61241a848b94647b6c7e6341f44374018da6cdb3`.

The installed adapter was executed with its network transport replaced by a
spy in the worker project's existing virtual environment. It forwards4,096,
records4,096, preserves the model/backend and returns response text unchanged.
This is a transport check, **not a model run or proof that a full file will now
finish**. Initial verification used system Python, which lacks MCP; an attempted
live-process interpreter lookup also found no matching argv. The actual check
used `/home/claude/fleet-worker-node/.venv/bin/python`, without installation.
No test files. [Deployment](evidence/20260921-worker-output-limit-deployment.json),
[transport evidence](evidence/20260921-worker-output-limit-transport.json).

Next: exercise the larger allowance on one genuinely new, useful whole-file
change, retain the raw candidate and independently verify it. Check requested
versus emitted token counts and time to first file.4,096 is still finite; a
larger file may need a bounded patch/chunk workflow. Do not infer a throughput
improvement before that real task, or repeat the completed HTML change solely
to manufacture a successful screenshot.

[Sanitized exact action/attempt trace](evidence/20260921-worker-action-trace.json).
Private reasoning was not extracted; only fixed log events and numeric metadata
were retained. No new inference, tests or model/topology changes for this diagnosis.

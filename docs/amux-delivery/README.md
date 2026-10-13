# Amux → HEARTH delivery: operator recipe

Two steps that stay separate:

1. **Prepare** (offline, this repo): `tools/ops/amux_delivery_prepare.py` turns a spec into the exact
   `submit_local_work` arguments. It reads git objects only. It does not dispatch, choose a lane, seat, backend or
   model, speak MCP, read route or qualification state, or record a verdict.
2. **Submit** (native HEARTH tools in the Amux worker session): the worker calls the existing
   `submit_local_work`, `watch_local_work`, `get_local_work`, `get_local_work_artifact`. The door alone decides
   route, qualification and capacity. A local CLI process is not evidence of local inference; only the manifest is.

## Recipe

```bash
~/.venvs/hearth-private/bin/python tools/ops/amux_delivery_prepare.py \
    docs/amux-delivery/specs/log-dedupe-review.spec.json --out /tmp/args.json
```

Exit 2 and `REFUSED: ...` on stderr mean the spec is wrong (intent without a `[goal:G-...] ` prefix, bad brief,
unreadable file or base, a git timeout, a pin field, an unbounded report). Fix the spec; nothing is repaired or
rewritten for you. On success stderr prints `prepared (not submitted)` with file hashes and the idempotency key.

1. Paste `/tmp/args.json` to the native Amux worker as the literal argument object of `submit_local_work`. Add no
   `lane`, `backend`, `model`, `max_tokens` or `procedure` (the spec has no fields for them).
2. Retry only with the same arguments (same key returns the same work). A new measurement sets a new
   `attempt` in the spec, which changes the key.
3. Show the state from `get_local_work(work_id)`:
   | `status` | meaning |
   | --- | --- |
   | `queued` / `running` | admitted; `route` names the admitted provider, which is not yet proof of execution |
   | `LocalWorkError` | the submission was refused. Report the text verbatim. It can also refer to existing idempotent work (e.g. a key reused for different work), so check `get_local_work` before saying no work exists |
   | `awaiting_review` | a candidate was stored. **This does not mean form or quotes passed** (see below) and it is not acceptance |
4. At `awaiting_review` show, separately: the original candidate from `get_local_work_artifact` (never an edited
   copy); the delivery record from the same call (`verification.deterministic` state and note, `unsupported`
   quotes, `repairs`, `deviations`); and provenance from the manifest: `route.provider`, `route.selected_model`,
   `route.serving_profile_sha256`, `route.procedure`, `route.procedure_choice`. `_finish_delivery` stores
   `awaiting_review` even when deterministic verification failed or quotes are unsupported, so read those fields;
   do not infer them from the status.
5. Actual execution is shown by a completed job with token and timing provenance (`route.tokens_in`, `route.tokens_out`,
   `route.duration_ms` on the completed work manifest; `attempts[]` include repair metadata and request/job identifiers) plus `route.provider`
   and the observed `route.backend`/`route.model`. The admission route alone is not execution. For AM4
   that means `route.provider == "am4-vllm"` with those figures, not anything the worker says.
6. Execution is not substance. Whether the report is right against the pinned source is read by the reviewer; the
   verdict is Derek's under PROD (`record_local_work_verdict`). A reviewer's summary may accompany the candidate if
   clearly labeled as a separate document. The original candidate stays unchanged and nothing is applied before the
   verdict.

Refusals you should expect today (all from the door, none from the preparer):
`no eligible backend in lane 'deep': {...}` with per-backend reasons, `lacks a minimum declared serving profile`,
`has no qualified delivery procedure for this serving profile`, `procedure '...' is not qualified for '...' at this
profile`, `backend '...' capacity exhausted`, `exact context refusal`, `output reserve N exceeds '...' declaration`.
In a pooled route a provider lacking qualification is excluded individually; the whole delivery is refused only when
no eligible provider remains. Pooled work without a brief is refused outright; the preparer always supplies one.

## Examples (pinned to `/home/derek/work/worktrees/amux-local-capability-20261013` @ `4f3c67f8888a8ed6698af1182d2e94d9ecf0f4d4`, read-only)

| spec | file |
| --- | --- |
| `specs/log-dedupe-review.spec.json` | `crates/amux-server/src/log_dedupe.rs` |
| `specs/listener-handoff-review.spec.json` | `crates/amux-server/src/listener_handoff.rs` |
| `specs/activation-check-review.spec.json` | `crates/amux-server/src/activation.rs` |

Each report is capped at 240 words, three explicit substance criteria, exact quotations, and
`brief.generation.max_tokens` 2048 and `words.enforce` `fail` (the door's own headroom for 240 words with quotes; the
preparer refuses less, and the contract bounds it at 16,384). Whether a provider can serve a given budget is the
door's per-provider admission decision, not the preparer's; a larger budget is accepted and no provider is pinned.
Every intent starts `[goal:G-ecosystem] `. Independent files, so concurrent submissions can
spread across qualified backends by the door's least-outstanding rule; do not add load to force a backend.

## Three-dense pool: staged, not activated

`three-dense-routes.NOT-ACTIVATED.toml` lists `omen-dense-27b`, `omen-dense-27b-b`, `am4-vllm`. The pool is not activated. Live routing is still
the singleton `omen-dense-27b`. A pooled backend is admitted only if its **exact** serving profile
(`LocalWorkService._serving_profile`, `serving-profile.v2` sha256 of the declared recipe) has at least two distinct
accepted briefs for the procedure (a provider without them is excluded; the delivery is refused only if none remains), recorded as verdicts for that backend and profile in the exported
`delivery-procedures.v1` table (`backends.<name>.profiles.<sha>`). Counts are never copied between replicas or
profiles; legacy unversioned fingerprints (`legacy_profiles`) cannot qualify a pool. Gate, from ADR-0062 and
`docs/bench27-capacity-runbook.md`:

1. Final recipes frozen; fingerprints computed with the actual helper.
2. Qualification laps on each backend through a temporary singleton route, reviewed and recorded as verdicts.
3. Exported table checked offline with `hearth.delivery.procedures.choose(..., require_profile=True)`.
4. Table and pooled route deployed together while admissions are paused. In the current running code the route and
   table are read at each submission, so this data-only change needs no door or GPU restart (a restart is needed only
   for code changes).

AM4 caveat: its declared window is 16,384 tokens, below the carried procedure's 24,576-token thinking turn, so carry
cannot run there. It can qualify only for `one_call`, and the door falls back to `one_call` only when that procedure
meets the threshold at AM4's profile.

## Qualification lap with `one_call`

`--one-call-qualification` adds `"procedure": "one_call"` to the arguments (and so changes the key). Use it only for an
explicit qualification lap on a singleton qualification route, kept in its own records. It does not make a backend
eligible: only recorded accepted verdicts do. Normal submissions omit it and take the door's selection.

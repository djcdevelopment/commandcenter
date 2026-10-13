# Amux → HEARTH delivery

**Status, 2026-10-13.** Real AM4 execution happened: the native Amux worker submitted HEARTH work that ran on AM4. A counted citation sidecar took one AM4 result from 1 to 0 unsupported quotes with its 227 words preserved (prose and wrong claims unchanged; semantic review still required). The offline preparer and sidecar are committed. An explicit-thinking adapter for AM4 is built and independently reviewed but **not deployed**. The three-backend pool is **staged, not activated**, and its capacity is unknown. No new production verdict was recorded and no candidate was applied.

Closure packet: [RESULT](RESULT.md) · [PLAN](PLAN.md) · [HANDOFF](HANDOFF.md) · [RETROSPECTIVE](RETROSPECTIVE.md) · [evidence index](evidence-index.json) · [evidence/](evidence/) · [operational-drafts/ (NOT ACTIVATED)](operational-drafts/README.md).

**Scoped thinking fix.** The AM4 facade defaults a missing `reasoning_effort` to `none`, which overrode an explicit `enable_thinking=true`. `hearth/toolsurface/inference.py` now sends `reasoning_effort="xhigh"` only when thinking is explicitly true, the backend is exactly `am4-vllm` and the model exactly `am4-dense-27b`, and only if the trusted serving declaration says `xhigh`. Otherwise it refuses before any network call. Other calls are unchanged, and no credential restriction changes. It takes effect only after a controlled door reload and a separate declaration decision.

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

AM4 caveat (the **current** declaration only): `am4-vllm` declares a 16,384-token window and no `deliberate_max_tokens`
(the "HEARTH 16K / no-deliberate" declaration). Under it the carried procedure's 24,576-token thinking turn does not
fit, so carry cannot be admitted there and AM4 can qualify only for `one_call`; the door falls back to `one_call` only
when that procedure meets the threshold at AM4's profile. This is an admission and profile limit, not a measured
hardware incapability. A proposed 49K/xhigh AM4 declaration has not been activated; if it ever is, it is a different
serving profile with its own fingerprint and its own qualification, and these statements would be revisited then.

Today's routing, for contrast: the deployed route is the singleton `omen-dense-27b` with the current table, where an
unpinned `code_review` delivery automatically selects `carry`. The staged pool remains unqualified and unactivated.

## Qualification lap with `one_call`

`--one-call-qualification` adds `"procedure": "one_call"` to the arguments (and so changes the key). Use it only for an
explicit qualification lap on a singleton qualification route, kept in its own records. It does not make a backend
eligible: only recorded accepted verdicts do. Normal submissions omit it and take the door's selection.

## Enabling ladder and the form-repair sidecar

Before calling a miss a capability limit, climb in order and keep every rung's result, including the failures:

1. **Record the baseline.** The original candidate, its delivery record and the brief hash stay as they are. Nothing below replaces or regrades them.
2. **Right-size** the task, source and budget. A dense three-criterion review is a prospective, separately named condition: up to 600 words (target 400-500) and about 3,072 final-output tokens in `brief.generation.max_tokens`, subject to each route's own ceiling (the door decides per provider). That figure is the brief's generation request, used by one-call delivery. `carry` uses procedure-owned stage budgets (work, check, and attachment batches with their own limits) and does not promise that 3,072 caps its final generation; the report word limit is measured separately. It is a new condition, not a regrade of the 240-word attempts and not evidence that length caused any reasoning error. The 240-word example specs are unchanged.
3. **Use the supported thinking procedures** (carry, and `inference.deliberate` where a seat declares it) before adding anything of your own.
4. **Apply counted form sidecars** (below), as separate derivatives.
5. **Judge substance independently**, against the pinned source, by someone other than the sidecar. Mechanical success is never semantic acceptance.
6. **Preserve the misses**: unresolved quotes, word overflow and wrong claims stay in the record.

### `tools/ops/amux_delivery_sidecar.py RUN_DIR NEW_OUT_DIR`

Offline, no inference or dispatch. `RUN_DIR` is read only: `work-manifest.json`, `delivery-output.json`, `delivery.json`, `candidate.md`. The sidecar refuses unless the output, candidate, delivery record, brief and every pinned source file match the hashes the manifest recorded. `NEW_OUT_DIR` must not exist (it is created exclusively), must not overlap `RUN_DIR`, and may not sit under `runs/`, `jepa-*`, `hybrid-context-trial` or the Windows read mounts. Exit 2 and `REFUSED: ...` on any refusal.

It writes `original/delivery-output.json` (byte copy), `delivery-output.derivative.json`, `candidate.derivative.md`, `delivery.derivative.json` (renderer manifest, `sidecar` aid added only if something changed) and `sidecar-report.json` (`semantic_review_required: true`, `official_verdict: null`, original/derivative SHA-256, brief hash, pinned commit and source hashes, exact diff, per-change log, unresolved list, before/after measures, and checks that prose, headings and paragraph order are identical).

The one repair, `symbol_scoped_quote`: a quote the renderer refuses as short and ambiguous is replaced by the shortest unique excerpt (inside one Rust function, at most 400 characters, never starting or ending on whitespace or inside a word or combining sequence) only when exactly one function named in the paragraph owns exactly one exact occurrence. Functions named but not containing the quote do not make a tie. Equal-length excerpts all contain the same owned occurrence; the earliest is taken and the count is logged. Everything else stays unresolved with a reason: several named owners, no named owner, duplicate hits inside the owner, no unique excerpt, a non-Rust source, or any source shape the stdlib scanner does not trust (unterminated strings or comments, raw identifiers, braces in generics, non-ASCII code tokens outside strings and comments). Blank quotes are kept in place and logged by both original and claim index. A wrong claim with a now-resolving quote is still a wrong claim: only the citation is repaired.

Word overflow is measured against the original brief and reported (`words_over_max`); nothing is trimmed, dropped or re-budgeted, and an over-budget derivative stays failed under `enforce: fail`.

Demonstrations (scratch only, originals untouched): `work_eb2686322ad4c268e43bddb78a783c49` resolves `s1.p0.q1` (`set_cloexec(fd, true);`, lines 61 and 88) to the `exec_failed` occurrence at line 61 and leaves its false claims as written; `work_dc8cb6b22991e89a02b11b8d6d8290c7` (246 words, max 240) comes back with zero repairs and an overflow of 6.

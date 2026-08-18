# 0031 — A pin chooses a rung; it does not waive arithmetic

Status: **Accepted** (Derek, 2026-08-06) · Cite as `commandcenter/docs/adr#0031`
Follows: `commandcenter/docs/adr#0021` (router fails closed), `commandcenter/docs/adr#0030`
(HEARTH is the system of record for AI execution), and the am4-oxen context-budget
correction (`0eeb1df`).

## Context

`select_backend` (`hearth/toolsurface/backends.py`) has four routing paths. Three of
them — `model=`, tag match, and the default/overflow ladder — compare the call's
`payload_bytes` against the candidate rung's declared `settings.context_bytes` and
skip or refuse when it does not fit. The fourth, the caller-pinned backend name, did
not. Verified at `59753c9`:

```
select_backend(pool, backend="am4-oxen", payload_bytes=80000)
  -> am4-oxen, reason "pinned:am4-oxen"      (am4-oxen declares context_bytes = 57344)
```

That call dispatched and failed at llama-server. The operator error therefore
presented as a server fault, which is how a wrong `context_bytes` on am4-oxen
(114688, roughly double the truth) survived for a month before `0eeb1df` corrected it.
`0eeb1df` fixed tag-routed traffic; because pins bypassed the check, it did not fix
the pinned case that produced the evidence in the first place.

The behavior was defensible, and `backends.toml` documented it: "caller-pinned backend
name -> wins outright, no occupancy check (a pin is deliberate)." The question was
whether that doctrine should extend from occupancy to payload size.

Two things decided it.

**Occupancy and payload are not the same kind of override.** `backends.toml` records
that a pinned call to a busy rung "dispatches and WAITS IN LINE in llama-server's
internal request queue." Overriding *busy* buys a real outcome: wait longer, get the
answer. Overriding *over-budget* buys nothing — no queue makes 80000 bytes fit in
57344. One is a judgment about scheduling; the other is arithmetic.

**A pin was already refusable.** `5e93883` added a check that raises
`BackendConfigError` when a pin names a model the rung does not provide. "A pin is
never refused" had already stopped being true, in the same branch, for the same reason:
the request cannot be served as stated.

The live instance was on the execution control plane. `hearth/etc/operations.toml`
gives `llm.chat` `max_prompt_bytes = 65536` while its `default_model`
(`gpt-oss-120b`) is served by am4-moe at `context_bytes = 57344`. A 60 KB prompt with
`model="gpt-oss-120b"` was already refused via the model path; the same prompt with
`backend="am4-moe"` was admitted, dispatched, and failed downstream. That 8 KB band
was reachable by any caller.

## Decision

**A name-pinned backend whose payload exceeds its declared `context_bytes` is
refused at the door.** `select_backend` raises `BackendRoutingRefusal` — the same
exception the overflow ladder already raises — with
`reason_code = "payload_over_budget_for_pinned_backend"` and a single `attempted` row
flagged `pinned: true`. The pin's occupancy override is untouched: a busy rung within
budget still dispatches and waits in the provider's queue.

Scope limits, deliberate:

- **The check is gated on `payload_bytes is not None`.** Callers that never supply a
  payload size — `hearth/toolsurface/build_requests.py` — keep their historical
  unconditional pin behavior. The router refuses what it can measure, and claims
  nothing about what it cannot.
- **A rung declaring no `context_bytes` is unlimited**, as before.
- **The comparison is strictly greater-than**: a payload equal to the budget is inside it.
- **The refusal is decided before the occupancy probe**, so an unreachable rung costs
  no round trip to refuse.
- **Model mismatch still wins ordering.** A pin naming both a wrong model and an
  over-budget payload reports the model error: the rung cannot serve that model at all.

On the execution plane the refusal lands at **admission** (`ExecutionService.submit`),
so no Job is created for work that cannot fit, and in `plan()`, so the content-free
dry run tells a caller a pin will not fit before any tokens are spent. An `endpoint=`
argument is converted to a name pin before routing (`service.py:222-230`) and is
refused on the same grounds.

**The exception describes itself.** `BackendRoutingRefusal.message()` renders the
payload and every rung the router weighed, and `__init__` passes it to
`super().__init__`, so `str(exc)` carries the numbers. That is what lets the
execution service stay **completely unmodified**: its pre-existing
`except BackendConfigError: raise ExecutionServiceError(str(exc))` already produces
a message a caller can act on. The first draft of this change instead added a
`_refusal_message` helper plus two `except BackendRoutingRefusal` clauses to
`hearth/execution/service.py`; that put knowledge of the `attempted` row shape at a
second boundary, for 36 lines that the exception could carry itself in 8. One
formatter, in the file that builds the rows.

**Refusal reasons are a family, and `errortax` must name it.** `hearth/errortax.py`
is the single taxonomy that maps an error string to a ledgered `error_code`, and
`hearth/kernel/gateway.py` re-derives that code **from the message text** rather than
reading the `error_code` the result already carries. Its needle was the literal
`payload_over_budget_no_eligible_backend`, so the new pinned reason classified as
`other`: an identical-in-kind refusal that the ledger could not count as a refusal.
The needle is now the family prefix `payload_over_budget`, and a test asserts both
codes classify as `routing_refusal`. Any future refusal reason must stay inside that
prefix — this is the trap that makes adding a reason code look free when it is not.

## Consequences

**Every `context_bytes` value in `backends.toml` is now load-bearing.** This is the
real cost, and it is not small. Those numbers are hand-derived from a rung's `-c`/`-np`
at roughly 4 bytes per token, and one of them has already been wrong by 2×. Before
this change a stale value cost a call that would have failed anyway. Now an
over-conservative value **blocks a call that would have succeeded**, and the pin — the
operator's escape hatch — is exactly what no longer overrides it. The mitigation is
that a wrong value is now loud instead of silent, and the fix belongs in
`backends.toml`, which is where the truth was supposed to live. `backends.toml`
carries this warning inline next to the routing policy.

**Known headroom to watch:** `hearth/experiments/doc_adr_bench.py` pins its sweep at
`["gcp-gemini", "gcp-gemini-pro", "am4-moe"]`. All five tasks still route, but its
largest (`adr-vs-code-container-access`, which packs `hearth/kernel/gateway.py`)
measured **53380 bytes against am4-moe's 57344 — 93% of budget, under 4 KB of
headroom**. It was 50967 B (89%) a week earlier, before the execution-plane commit
grew `gateway.py`; the margin is closing at roughly the rate that file changes. The
next few KB of growth there turns a working bench into a refusal. That is the intended
behavior, but it will look like a regression to whoever hits it first — the fix will
be to drop the `am4-moe` pin from `BACKENDS`, not to widen `context_bytes`.

**Not changed, and why:**

- The execution service's payload measurement was already correct. `submit()` packs
  `files` at admission, pops the key, and recomputes `prompt_bytes` from the packed
  prompt (`service.py:200-215`), so the outer and inner routers see identical byte
  counts. There is no double-accounting to fix.
- The unconditional `backend=provider.name` re-pin at `service.py:446` is left alone.
  Because the two measurements agree, it cannot convert a fitting router choice into a
  failing inner pin.
- `local_generate` needed no change: it already catches `BackendRoutingRefusal` and
  returns `ok:false` with `error_code: "routing_refusal"` and the full structured
  refusal. It renders its own terser `f"routing refused: {exc.reason_code}"` rather
  than calling `message()`, which is fine because it ships the `routing_refusal` dict
  alongside — the numbers are not lost, only spelled differently.
- `required_context_bytes` has never carried information distinct from
  `payload_bytes` at any of the three raise sites. Left alone: it is a required field
  of `contracts/hearth-event.v1.schema.json`, and collapsing it is a wire-contract
  change that has nothing to do with pins.
- The `attempted` row dict is now built inline at five sites in `backends.py` with no
  shared constructor, and the shape has already drifted (`ladder` on two, `pinned` on
  one). Worth a constructor eventually; not worth widening this change to five call
  sites. Keeping `message()` in the same file as the builders is the interim guard.

**Blast radius at adoption: zero existing callers broke.** All 791 tests pass
unchanged. Every prior pin assertion either used a fixture pool with no `context_bytes`
or a payload small enough to fit. The execution-plane pin path was previously
untested; it now has coverage for the refusal, the fitting pin, the unpinned control,
the `endpoint=` conversion, and both `plan()` outcomes.

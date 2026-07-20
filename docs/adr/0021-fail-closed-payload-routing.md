# 0021 — Payload budgeting fails closed at the inference router

**Status:** Accepted (2026-07-20)

## Decision

When a request exceeds a backend's declared `context_bytes`, that backend is
not eligible. The router may continue through the declared `big-context` and
`cloud-overflow` ladder, preserving existing healthy overflow routing, but it
must refuse if no candidate fits and has verified available occupancy.

The refusal reason is the stable machine value
`payload_over_budget_no_eligible_backend`. The result carries the measured
`payload_bytes`, `required_context_bytes`, default capacity, and every attempted
ladder rung with its capacity, occupancy, and rejection reason. The ledger
records the same object under `routing_refusal` and stamps `error_code` as
`routing_refusal`.

An occupancy value is only `available` when the router has either received that
value from its injected probe or is operating in the explicit no-probe unit
test mode. Missing or malformed probe output is `unknown`, never available.

## Consequence

The former `default:overflow` terminal fallback is removed. This is intentional:
ADR-0018 records that the local resident server silently truncates over-limit
prompts, so routing an over-budget request to the default is a correctness
failure, not graceful degradation.

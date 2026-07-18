# 0018 — The resident big-MoE is oxen's steady-state tenant: goodput-routed, budget-enforced

**Status:** Accepted (2026-07-18) — live on master (`384d9fb`), receipts
`br-20260718-064639-50a1d2f7` + `br-20260718-074554-fe90b0be` done.

## Context

The dual-B70 node ("oxen") spent most days with one card serving a 30B planner and the other
idle — 64 GB of VRAM as banked fire nobody drew on. Derek's design question: soak that VRAM
with one large resident MoE that serves as mechnet's always-warm router/worker, accepting
queue-and-wait semantics, with KV/slot polling to manage goodput. O1's link measurements
(full x8 wire speed P2P, free copy engine, ~6 µs floor) said the hardware supports the shape;
the gambit (453/453, no breaking point found) said the software does too.

Three facts discovered on the way constrain the design:

1. **llama-server silently truncates over-limit prompts** (a 17.5k request against a 16k slot
   returned `ok` with no signal) — the server will *degrade*, not refuse.
2. **A resident server breaks render-owner occupancy semantics** — the fuser probe reads its
   own always-on tenant as "busy forever".
3. **The two serving cards cannot host a second LLM tenant** — gpt-oss-120b + `--n-cpu-moe 4`
   is a fit at the edge of 2×30.3 GiB; the planner/critic cannot co-reside.

## Decision

1. **gpt-oss-120b is the steady-state tenant of both B70s** (`b70-moe.service`, `:8082`,
   enabled + linger). The former tenants demote: `am4-oxen` (planner) loses its opportunistic
   tags and its `revive` hook — it is **pin-only**, and a deliberate planner run requires
   stopping `b70-moe` first. Auto-revive is removed *because* an automatic wake would start a
   model into the resident tenant's VRAM.
2. **Occupancy for the resident rung is slot/KV goodput, not hardware ownership.**
   `occupancy.py` carries a probe registry: `am4-oxen` keeps the fuser probe, `am4-moe` gets
   an HTTP `/slots` probe — saturated slots (or KV past pressure ceiling, when the build
   exposes it) reads "busy" so opportunistic traffic steers to omen/flash; pinned calls
   dispatch regardless and wait in llama-server's internal queue (proven fair to 8×
   oversubscription).
3. **Door-side payload budgets are load-bearing.** Because the server truncates silently, the
   rung's `context_bytes` (57344 ≈ 14k tokens) is the *only* thing preventing invisible
   context loss. A1 payload-aware routing enforces it; nothing may bypass the door to `:8082`
   for real work.
4. **Task-shape doctrine for drafting offloads:** the resident brain takes reasoning-dense,
   short-output work (≤ ~2.5k output tokens/call — its ~27 tok/s including hidden reasoning
   meets the client timeout layers); bulk long-form generation goes to the flash rung
   (10× decode, but budget its hidden thinking — expansions ≥2k tokens may need two passes).
   Review verifies *context claims* from both, not just numbers (extends ADR-0004).

## Consequences

- The mechnet ladder reads: **am4-moe resident brain → omen-ollama fast small-model swaps →
  gcp-gemini trial credits → fleet backlog.** Research/second-opinion/reasoning tags route to
  the brain organically (proven 3/3 on first real workload).
- Imagegen and any other both-cards workload now require freeing the tenant — the "mechnet
  modes" scheduling primitive (GpuLaunch/Allocator seed) is the designed follow-up, not an
  incidental conflict.
- **New-rung onboarding checklist** (tonight's session missed one of these until the
  scorecard exposed it): `backends.toml` entry with `context_bytes` + `max_tokens` · occupancy
  probe registered (or explicitly none) · `economics.py` `COST_CLASS_MAP` entry · timeout
  guidance for callers · fleet inventory/checkmechnet visibility · doorcheck green after
  restart.
- Reversal path: stop/disable `b70-moe`, restore `am4-oxen` tags + revive, re-enable
  planner/critic units — all config, no data loss; the model file stays in the 4TB zoo.

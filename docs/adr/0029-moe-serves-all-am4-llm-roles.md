# 0029 — The resident MoE serves all AM4 LLM roles; planner/critic retired from service

Status: **Accepted** (Derek, 2026-07-30) · Cite as `commandcenter/docs/adr#0029`
Follows: `commandcenter/docs/adr#0018` (resident-moe steady state), the 2026-07-30
tenancy-standoff incident (`B70-VERTICAL-TRACE.html`, repo root), and the same-day
unit hardening (`309b7fc`, `bbba891`).

## Context

ADR-0018 made gpt-oss-120b (`b70-moe`, dual-card, :8082) the steady-state tenant but
left `b70-planner`/`b70-critic` enabled as "managed claimants" of :8080/:8081. That
latent second topology produced the 2026-07-30 standoff: 81 OOM crash-loop restarts,
both tenants taking turns as the OOM victim, monitoring green throughout. Same-day
hardening added `Conflicts=`/start limits, and a ratified interim topology (moe at
boot, planner/critic on demand for pours) surfaced an unresolved gap: after a pour,
nothing brings the moe back.

## Decision

**Cut planner/critic from service entirely; the moe serves both roles.** Pours and
any planner/critic-shaped work ride the moe's 4 slots + internal queue.

Executed 2026-07-30:

- Unit files moved to `~/.config/systemd/user/retired/` on AM4 (+`daemon-reload`);
  `systemctl --user start b70-planner` now fails `Unit not found`. Files remain on
  the box and mirrored in `am4-fleet-node/systemd/`. (`mask` was not usable: the
  unit file occupies the mask path, and `mask --force` would overwrite it.)
- `wake_am4` (hearth/toolsurface/summon.py) retargeted: starts `b70-moe`, keys
  serve-truth on llama-server `/health` :8082 (auth-exempt; 503 while loading —
  port-open is never readiness), default `wait_s` 120→360 (cold ntfs3 load 3–5 min),
  imagegen gate unchanged, render-node occupancy probed directly
  (`probe_render_owners`) rather than via the `_PROBES["am4-oxen"]` registry entry,
  which is being repointed independently.

## Consequences

- The "moe does not auto-return after a pour" question **dissolves** — no swap
  occurs. `OnSuccess=` stays rejected.
- `Conflicts=` becomes a backstop, not a working edge: live-fire proven during
  execution (an ungated start-proof swapped tenancy and cost ~7 min of moe outage —
  the guardrail behaved exactly as designed; the operator lesson is to gate proofs
  on the guard actually landing).
- Revival ceremony (deliberate only): `mv` the unit file back from `retired/`,
  `daemon-reload`, stop `b70-moe`, start the revived unit. Enablement policy stands:
  exactly ONE topology in `default.target.wants`.
- **Follow-up — oxen facade lane:** `am4-oxen-facade.service` (:8090) now fronts a
  dead loopback port, and the `am4-oxen` rung in `hearth/etc/backends.toml` (plus
  its occupancy probe) points at it. Ledger mentions of `am4-oxen` persist via
  health probes, so disposition needs a *dispatch-filtered* ledger query. Decide
  after the in-flight context_bytes/probe fix lands: retire the rung + facade, or
  repoint the facade at :8082 (requires the facade to attach the moe bearer).
- **Long-term direction (Derek):** the real fix is scheduling capability — treat
  AM4 workloads as a job-shop (load time, active time, swap time). Today produced
  the missing coefficients: cold load 190–320 s (ntfs3, cache-dependent), warm-cache
  load ~2–3 min, `/health`-gated readiness, per-topology VRAM/RAM budgets, OOM-risk
  boundaries. Gate for that work: consult `SCHEDULER-STRATEGY.html` first;
  `commandcenter/docs/adr#0008` (advisory-first) governs.

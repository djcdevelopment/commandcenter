# ADR-0013 — wake_am4 goes live: serve-truth idempotency, queue-gated occupancy, one systemd claimant per port

Status: Accepted (2026-07-07)

## Context

`wake_am4` shipped in Stream H-B as a stub returning `{ok:false, stub:true, would_run:…}` — the
correct tool *shape*, with execution deferred to H3 (see [BUILD-NOTES-HB](../../BUILD-NOTES-HB.md)).
Flipping it live had to reckon with ground truth that had moved since the shape was authored:

- **AM4 is now native Ubuntu**, and its inference muscle is a systemd `--user` slot unit,
  `b70-planner.service` (Qwen3-30B, single-card SYCL0), fronted by the always-on
  `am4-oxen-facade.service` on `:8090` whose `/health` reports `backend.ok` — the serve-truth for
  `:8080`. A sibling `b70-critic` unit owns SYCL1. Both are enabled + lingered (survive reboot); see
  [[reference-am4-b70-cards]].
- **The B70s are shared with image generation.** ComfyUI holds *both* render nodes 24/7 even when
  idle, so a bare "is a render node held?" check would refuse every wake.
- **The stub's own command was stale** — it named `am4-hermes-backend.service` / `am4-planner` /
  `am4-critic`, units that were retired. The `am4-planner`/`am4-critic` units still existed as
  hermes-era zombies pointing at a deleted launcher; they had crash-looped **58,922 times** (every
  5s for ~3.4 days) before being disabled 2026-07-07.

The brief-of-record suggested waking via `nohup ~/baseline/relaunch-qwen3-baseline.sh &` over SSH.
The newer B70 runbook forbids exactly that (gotcha #2: SSH-detach swallow), and that relaunch
script's dual-card split (`-dev SYCL0,SYCL1`, 131k ctx) is an experiment-throughput mode that grabs
*both* cards — it would stomp the critic slot and imagegen.

## Decision

**Make `wake_am4` an idempotent, occupancy-gated wake of the one managed planner unit — and add no
second latent launcher for `:8080`.**

- **Serve-truth first (idempotent).** GET the facade `:8090/health`; if `backend.ok` is already
  true, return `already-serving` as a no-op. The tool is safe to call unconditionally.
- **Occupancy gate on the ComfyUI queue, not on holder-presence.** Because ComfyUI holds the render
  nodes even when idle, holder-presence cannot refuse a wake. Instead, when imagegen markers hold the
  cards, `_imagegen_active()` checks the ComfyUI `:8188/queue`: only a **busy or unverifiable** queue
  refuses (`force=True` overrides). Our own `llama` slot holders (the resident critic, or the planner
  mid-load) never block — starting the managed unit alongside them is the designed, idempotent
  layout.
- **Start the managed unit, never nohup, never the dual-card script.** Wake = `systemctl --user
  start b70-planner` over SSH (reusing the occupancy module's one SSH discipline), then poll facade
  health until `backend.ok` or `wait_s` elapses (`wait_s=0` = fire-and-forget).
- **One systemd claimant per port.** No dedicated banked-fire unit was created for a dual-card
  baseline: `:8080` already has a systemd claimant (`b70-planner`), and a second latent claimant
  invites unit-vs-unit races — the 58,922-restart zombie crash-loop is the standing evidence of what
  those cost. Fewer latent launchers on a shared-GPU box is the rule.

The richer alternative — a thermal occupancy gate (hwmon: idle B70s ~50°C, imagegen pushes high-80s
within seconds; a ~70°C threshold catches *any* GPU work, not just ComfyUI queue entries) — was
prototyped and **deliberately set aside**: it was unwired and untested when picked up, and both the
test contract and the shipped behavior are queue-based. It remains recoverable from history as a
proper, tested follow-up if the queue signal proves too narrow.

## Consequences

- **Good:** the `am4-oxen` backend now has a real, ledgered revive that is safe to call
  unconditionally, cannot stomp an in-flight render, and cannot leave a second launcher fighting the
  managed unit for the port. Proven live end-to-end (cold planner → serving in 26s; and the
  idempotent no-op path verified through the live door). Reuses the occupancy SSH/probe discipline
  rather than growing a second one ([ADR-0005](0005-one-boundary-three-planes.md)).
- **Cost / open:** the gate trusts the ComfyUI queue as the imagegen liveness signal — a non-ComfyUI
  GPU job (a manual torch-xpu run) would read as idle. The thermal gate would close that gap; it is
  the named follow-up. `start_ollama` and `checkpoint_vm` remain stubs.
- **Boundary preserved:** occupancy-refusal carries the same co-tenancy safety posture as AM4-MCP's
  `start_oxen_backend` — refuse when a render is genuinely in flight, unless explicitly forced.

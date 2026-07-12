---
name: checkmechnet
description: Health-sweep the whole mechnet — every physical host, builder VM, logical builder, cloud backend, and the HEARTH door. Use for "fleet status", "is <node> reachable", "check the mechnet", or before dispatching fleet work.
---

# checkmechnet — sweep the mechnet

The mechnet = the ecosystem of distributed builders, VMs, and cloud backends:
physical hosts (omen, cc-conductor, am4, i5-laptop), OMEN Hyper-V VMs
(claudefarm1, cc-builder-1..4), logical builders (omen-worker-1,
am4-worker-1), and cloud backends (gcp-gemini — Vertex overflow lane on GCP
trial credits). Canonical node map: [fleet/inventory.toml](../../fleet/inventory.toml).

Run both, from the repo root (`C:\work\commandcenter`):

```
./fleet-worker-node/.venv-omen/Scripts/python.exe -m fleet.fleet_ping --all-services --no-color
./fleet-worker-node/.venv-omen/Scripts/python.exe -m hearth.callers.doorcheck --revive
```

- `fleet_ping` = reachability (TCP) of every declared service on every node.
  Exit 1 means some `expect="up"` node is down. `--json` for machine-readable;
  `--node <name>` to probe one node.
- `doorcheck` = the deep MCP-layer check of the HEARTH gateway (fleet_ping only
  proves :8710 accepts connects). See the `checkmcp` skill for its details.

## Interpreting downs

- **i5-laptop / cc-builder-4 offline** — expected-optional, not a problem.
- **A VM down** — check the node's `note` in inventory.toml first; known
  history lives there (e.g. claudefarm1's static-MAC collision with
  cc-builder-4, fixed 2026-07-04 — cc-builder-4 still holds the golden MAC).
  Diagnose from OMEN: `Get-VM` state, then mshome.net DNS, then ssh.
- **omen-worker-1 down but ollama up** — it rides claudefarm1's shell; fix
  claudefarm1, not the model backend (known coupling, see inventory note).
- **cc-conductor down** — the dashboard/API on :8080 and ssh on :22 are
  separate checks; one down without the other points at the service, not the
  network.
- **vertex-egress down** — a DNS/egress problem on OMEN reaching
  aiplatform.googleapis.com, NOT an auth failure; auth/quota truth lives in
  /checkmcp's backends layer (doorcheck reads hearth/etc/backends.toml). The
  node is `expect="optional"`, so it never fails the sweep — treat a down as a
  network diagnostic, not an incident.

## Routing economics (where to send work)

| tier | cost | send it |
|---|---|---|
| qwen3-coder:30b on OMEN | sunk | drafts, summaries, commit messages, triage |
| gcp-gemini (Vertex) | GCP trial credits — near-free while they last | infra tests, integration proofs, self-contained builds; pin `backend="gcp-gemini"` |
| Sonnet agents | metered | multi-file builds against a written brief |
| Fable | scarce | briefs, review, judgment — never grunt work |

## Reporting

Give the user a short table: node | status | note-if-down, then a one-line
verdict ("mechnet healthy" / "N nodes down: ..."). Fix nothing beyond the
doorcheck auto-revive unless asked — diagnosis first, per the
do-exactly-what's-asked rule.

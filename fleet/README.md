# fleet/ — reachability inventory + health sweep

The canonical, **OMEN-side** answer to "what can I reach on the fleet right now?"

Lives on OMEN (not the conductor) on purpose: OMEN is the durable Hyper-V host and the
best vantage point — it sees the tailnet *and* its own VM siblings. The conductor's
`fleet.json` / `fleet-dashboard.html` are the build-pool view and go dark when the
conductor does; this survives that.

## Files

- **`inventory.toml`** — every reachable node: address, how-to-reach (tailnet / mshome /
  nat-ip), the service port(s) that should answer, and what it's for. Hand-maintained;
  the stable reachability superset of the conductor's auto-churned `fleet.json`.
- **`fleet_ping.py`** — stdlib CLI that TCP-sweeps every node's service port(s) in
  parallel and prints up/down/latency. No dependencies (parses TOML with stdlib
  `tomllib`, Python 3.11+).
- **`test_fleet_ping.py`** — unit tests for the pure logic (no network).
- **`fleet_dashboard.py`** — the operator viewport: runs the same sweep and renders it as
  one static HTML page, **`FLEET-DASHBOARD.html`** at the repo root (the HEARTH-DASHBOARD
  precedent — inline CSS, no JS, theme-aware, 5-min browser refresh). Optional rung-state
  block (ADR-0044 omen-arc verdict) when `hearth.health.rungstate` is importable.
- **`test_fleet_dashboard.py`** — rendering + CLI tests with injected probers (no network).

## Usage

```bash
python fleet/fleet_ping.py                 # primary reachability of every node (table)
python -m fleet.fleet_ping --all-services  # probe EVERY declared service, not just primary
python -m fleet.fleet_ping --node claudefarm1
python -m fleet.fleet_ping --json          # machine-readable (for a dashboard/monitor)
python -m fleet.fleet_ping --timeout 2 --no-color

python -m fleet.fleet_dashboard                          # sweep + write FLEET-DASHBOARD.html
python -m fleet.fleet_dashboard --all-services           # every declared service as sub-rows
python -m fleet.fleet_dashboard --json-out fleet.json    # keep the raw feed beside the page
python -m fleet.fleet_dashboard --no-rung-state          # liveness only, skip the rate-health read
```

Exit code is **1** if any `expect="up"` node is unreachable (so it can gate a script or a
cron health check); nodes marked `expect="optional"` (e.g. the offline i5 laptop, the
overnight critic) never trip the exit code.

## Adding / changing a node

Edit `inventory.toml` — append a `[[node]]` block. Fields: `name`, `kind`
(`physical-host` | `vm` | `logical-builder`), `address`, `via`, `expect`
(`up` | `optional`), `purpose`, and a `checks` list of `{ service, port, host? }`
(a check's `host` overrides `address` — used by logical builders whose shell and model
backend live on different machines). Run `python -m unittest fleet.test_fleet_ping` after.

## Known reachability facts (baked into the inventory)

- **VMs never join the tailnet** — same-host VMs are reached via `mshome.net` sibling DNS;
  OMEN (the host) resolves them directly.
- **Logical builders ride a shell host.** `omen-worker-1` runs on `claudefarm1` but uses
  OMEN's Ollama — so it dies when `claudefarm1` is down even though its model backend is
  fine. `am4-worker-1` rides `am4`.
- VM NAT IPs (e.g. `cc-builder-4`) drift on OMEN reboots; prefer the `mshome.net` name
  where one exists.

## Fleet dashboard (built 2026-09-03, M4)

`fleet_dashboard.py` is the slice this README used to promise: the `--json` feed plus a
static HTML page, OMEN-hosted, independent of the conductor.

- `render_fleet_html(sweep, generated_at, extras=None) -> str` is pure: it takes the
  `fleet_ping --json` document (`{summary, nodes}`, or a bare row list) and returns the
  whole page. `extras` may carry `rung_state` (P7's dict), `inventory_meta`,
  `all_services`, `timeout`, `notes`.
- `main(--out, --all-services, --timeout, --json-out, --no-rung-state)` sweeps and writes
  `FLEET-DASHBOARD.html` at the repo root (a generated file — regenerate it, don't
  hand-edit). Exit 0 once the page is written; the reachability *gate* stays with
  `fleet_ping`'s exit code. `--json-out` keeps the raw feed (`generated_at`, `summary`,
  `nodes`, `meta`, `rung_state`).
- The **rung-state block** shows the omen-arc verdict from the keep-alive tail against
  the FF baseline epoch (`at_rate | warn | degraded | stalled | stale | unreachable`).
  A port that answers a TCP connect is UP to the sweep and can still be DEGRADED
  here — the page shows both on purpose (port-open ≠ model-ready). The read is
  best-effort: missing module or a raise → "unavailable", the page still renders.

Follow-up (not built): a refresh leg in `mechnet_watchdog.py` so the page regenerates on
the 15-min pass; until then, run the command by hand or from a scheduled task.

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

## Usage

```bash
python fleet/fleet_ping.py                 # primary reachability of every node (table)
python -m fleet.fleet_ping --all-services  # probe EVERY declared service, not just primary
python -m fleet.fleet_ping --node claudefarm1
python -m fleet.fleet_ping --json          # machine-readable (for a dashboard/monitor)
python -m fleet.fleet_ping --timeout 2 --no-color
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

## Next slice (not built yet)

A `--json` feed + a static HTML page = an OMEN-hosted fleet dashboard that doesn't depend
on the conductor. `fleet_ping.py --json` is already the data source for it.

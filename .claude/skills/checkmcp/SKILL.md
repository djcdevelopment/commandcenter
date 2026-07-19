---
name: checkmcp
description: Check (and revive) the HEARTH gateway — the MCP door on OMEN :8710. Use when a HEARTH/local_generate call fails, before a batch of offloads, or when asked "is the door up / check mcp".
---

# checkmcp — is the HEARTH door open?

One command does the whole job (probe + auto-revive + door verdict):

```
./fleet-worker-node/.venv-omen/Scripts/python.exe -m hearth.callers.doorcheck --revive
```

Run it from the repo root (`C:\work\commandcenter`). It checks explicit health
facets. Default exit 0 means the door facet is healthy; `--strict` requires all
facets. Exit 1 means the requested facet is unhealthy; exit 2 means a hard
configuration failure:

- `process_listener` — TCP listener reachable.
- `authentication` — an authenticated MCP tool call succeeds.
- `mcp_surface` — MCP handshake and tool manifest match.
- `backend_dependency` — the configured default backend is ready; `cold` is a
  distinct advisory status in default mode.

Machine-readable output uses `facets` with stable names and statuses. Existing
top-level `gateway`, `mcp`, `toolsurface`, `backends`, and `ok` fields remain.

- **gateway** — TCP :8710; with `--revive` it relaunches the gateway as a
  DETACHED process if down (safe: the start script is idempotent, one instance
  binds the port). "revived just now" in the output means it WAS down.
- **mcp** — real MCP handshake + tool count/names. "degraded" = port open but
  handshake failing.
- **toolsurface** — the tool-name MANIFEST match, not just a count. The
  expected set is derived by parsing the `--providers` list straight out of
  `hearth/etc/start-hearth-gateway.cmd` and importing each provider's
  `get_tools()` — the manifest derived from start-hearth-gateway.cmd is the
  authority, not a hardcoded number anywhere in this doc. `toolsurface: 41/41
  match` = healthy; `toolsurface: STALE - missing N: ...` means the launcher
  was updated (new/changed provider) but the RUNNING process predates it — a
  registered-but-not-loaded door. A mismatch makes the overall verdict
  DEGRADED even though the port and handshake look fine.
- **backends** — one line per backend declared in `hearth/etc/backends.toml`:
  - `omen-ollama` (api=ollama, the pool default) — real `/api/version` check;
    `cold` is advisory for the default door check and fails `--strict` or an
    explicit `--facet backend_dependency` check.
  - `am4-oxen` (api=openai) — TCP-only reachability, INFORMATIONAL. AM4 sleeps
    by design (banked fire); `asleep` is expected, not a failure, and never
    affects exit.
  - `gcp-gemini` (api=gemini) — auth-only check (token from the backend's
    `auth_env` if set, else `gcloud auth print-access-token`). Reported as a
    WARNING (`auth ok (adc)` / `auth FAILED - <reason>`); never affects exit.
- **ledger** — timestamp of the newest event (staleness check, UTC).
- **build-reqs** — last line of the Hearth build-request ledger
  (`HEARTH_BUILD_REQUEST_DIR`, default `C:\work\comfy\fieldlab\runs\build-requests`):
  `build-reqs: <receipt_id> <status> (<age>)`, or `build-reqs: no lane` if the
  dir/ledger doesn't exist yet. Informational.

Since ADR-0015 the gateway also hosts the repeating ops loops (patrol 5m, watchdog
15m, bankedfire drain 30m) as internal timers — so a door that's DOWN means those
loops are dark too, and a `--revive` re-arms them automatically (the start script
prints `hearth timers armed: ...`). Tick logs stay at `hearth/var/{watchdog-patrol,
watchdog,bankedfire-drain}-task.log`.

## Flags

- `--revive` — if the door is fully DOWN (port closed), relaunch it detached
  and re-check. Safe/idempotent; does nothing if already up.
- `--restart` — for a STALE or wedged door (port open, but toolsurface
  mismatched or the handshake is misbehaving). Fires the on-demand
  `HearthGatewayRestart` scheduled task (RL HIGHEST, S4U — the only reliable
  bounce from a medium-integrity shell; see Durable start below), polls the
  port until it has cycled down and back up (~25s budget), then re-handshakes
  and re-verifies the manifest, printing the fresh report. Never attempts to
  kill the process directly — you can't: `HearthGatewayBoot`'s python child is
  high-integrity and a normal shell gets Access Denied.
- `--probe-cloud` — additionally fires ONE real `gcp-gemini` generate
  (`local_generate(prompt="ping", ..., max_tokens=8)`) to prove the cloud
  backend actually answers, not just that auth resolves. Off by default — it
  spends a trickle of GCP trial credit. Run this before pinning
  `backend="gcp-gemini"` for real work.
- `--json` — machine-readable report with every field above.
- `--facet {door,process_listener,authentication,mcp_surface,backend_dependency}`
  — answer one requested facet; `door` is the default.
- `--strict` — require every facet, including backend readiness, to pass.

`GET /healthz` is unauthenticated and intentionally returns only
`{"status":"ok"}`. It reveals no caller data, tools, backends, paths, secrets,
or configuration.

Path inputs containing `..` are refused outright rather than normalized away.
This is an intentional hardening incompatibility: callers must provide direct
paths inside their granted scope. Do not weaken this rule for speculative
external consumers.

## Durable start (no stale claims here — this is the actual topology)

Two scheduled tasks are already registered; checkmcp does not create them:

- **HearthGatewayBoot** — boot trigger, S4U, RunLevel HIGHEST (needs Hyper-V
  admin for `checkpoint_vm`). This is the durable, survives-reboot start. Its
  python child is high-integrity — it cannot be killed from a normal shell.
- **HearthGatewayRestart** — on-demand, RL HIGHEST, no trigger. Kills the
  :8710 process tree and re-triggers `HearthGatewayBoot`. This is the UAC-free
  bounce path (`schtasks /Run /TN HearthGatewayRestart`), wrapped by `--restart`
  above. It is the ONLY reliable bounce from medium integrity.

A `--revive` (detached relaunch) survives closed consoles but NOT
logoff/reboot on its own — `HearthGatewayBoot` is what makes a reboot durable,
and it's already registered. Don't re-propose registering a logon task; that
was the old (wrong) fix.

## Reporting

Tell the user the verdict in one line, plus:
- if it was down and revived: what state it was found in.
- if `toolsurface` was STALE: name the missing/unexpected tools and suggest
  `--restart` (the door needs a bounce to pick up the new provider set).
- if a backend WARNING fired (gemini auth) or an INFORMATIONAL line is
  interesting (am4-oxen asleep): mention it, but don't treat it as a failure.

## Activation triggers

Run `/checkmcp` (with a plain re-check, no flags needed unless something's
wrong):
- after ANY commit that touches `hearth/toolsurface/*` or
  `hearth/etc/start-hearth-gateway.cmd` — the toolsurface layer exists
  precisely to catch a registered-but-not-loaded door in this situation.
- before pinning `backend="gcp-gemini"` on a real call — confirm auth resolves
  (and consider `--probe-cloud` once to confirm the endpoint actually answers).
- before a batch of offloads, or whenever a HEARTH/local_generate call fails.

## Known failure history

- 2026-07-03: gateway died silently — it had been started from an interactive
  console (the scheduled task was never registered) and died with that console.
  No "exited with" line in hearth/var/gateway-task.log = external kill, not a
  crash. Check that log's tail when diagnosing anything unexpected.
- 2026-07-12: a gateway upgrade added a 15th provider
  (`hearth.toolsurface.build_requests`, 6 tools) to
  `start-hearth-gateway.cmd`, but the running process predated it: the door
  served 35/41 tools for hours while the OLD doorcheck (raw tool COUNT only,
  no name manifest) reported HEALTHY. The toolsurface manifest layer — parsing
  `--providers` from the launcher and diffing tool NAMES against the live
  handshake — exists because of this incident, and `--restart` exists so the
  fix is one command instead of a manual elevated bounce.

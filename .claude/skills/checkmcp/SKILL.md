---
name: checkmcp
description: Check (and revive) the HEARTH gateway — the MCP door on OMEN :8710. Use when a HEARTH/local_generate call fails, before a batch of offloads, or when asked "is the door up / check mcp".
---

# checkmcp — is the HEARTH door open?

One command does the whole job (probe + auto-revive + verdict):

```
./fleet-worker-node/.venv-omen/Scripts/python.exe -m hearth.callers.doorcheck --revive
```

Run it from the repo root (`C:\work\commandcenter`). It checks four layers and
prints a verdict; exit 0 = HEALTHY, exit 1 = DEGRADED:

- **gateway** — TCP :8710; with `--revive` it relaunches the gateway as a
  DETACHED process if down (safe: the start script is idempotent, one instance
  binds the port). "revived just now" in the output means it WAS down.
- **mcp** — real MCP handshake + tool count (should be ~21 tools). "degraded"
  = port open but handshake failing; restart won't be attempted automatically —
  kill the listener on :8710 first, then rerun with `--revive`.
- **ollama** — the local_generate backend on :11434. If DOWN, the door is open
  but inference dispatches will fail; start Ollama (`ollama serve` or the
  gateway's `start_ollama` tool from another caller).
- **ledger** — timestamp of the newest event (staleness check, UTC).

## Reporting

Tell the user the verdict in one line, plus — only if it was down — what state
it was found in and that it was revived. If it revived, remind them: a revive
survives closed consoles but NOT logoff/reboot; the durable fix is registering
the `HearthGateway` logon task (see hearth/etc/start-hearth-gateway.cmd header).

## Known failure history

- 2026-07-03: gateway died silently — it had been started from an interactive
  console (the scheduled task was never registered) and died with that console.
  No "exited with" line in hearth/var/gateway-task.log = external kill, not a
  crash. Check that log's tail when diagnosing anything unexpected.

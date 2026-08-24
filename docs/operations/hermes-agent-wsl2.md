# Hermes Agent on OMEN — WSL2 evaluation instance

**Status:** live, evaluation only. Not integrated with HEARTH (no caller minted,
no rung registered). Built 2026-08-24.

**Why it exists:** to find out how much of mechnet's agent-execution plumbing
Nous Research's [Hermes Agent](https://github.com/NousResearch/hermes-agent)
makes unnecessary. See the "build less mechnet" reasoning at the end.

**Why WSL2 and not native Windows or a Hyper-V VM:** OMEN's Windows install is a
waypoint — the box is going to Linux for the Intel driver stack. Hermes lives
entirely in `~/.hermes/`, so installing into a WSL2 Ubuntu distro means that
directory is already Linux-native and moves to the rebuilt box intact, carrying
config, memory, self-written skills and session history. Native Windows is also
early-beta upstream for gateway/cron/long-task workloads and loses the dashboard
terminal pane (needs a POSIX PTY). A Hyper-V VM would be real isolation but is
pure Windows-shaped work the migration discards.

---

## What was built

| | |
|---|---|
| Distro | `hermes` — Ubuntu 26.04, `wsl --import`-style install at `E:\wsl\hermes` |
| Why E: | C: is at ~12.5% free with every VHDX **and** Derek's `Ubuntu` distro on it |
| Hermes | **v0.20.5 (v2026.8.19)**, pinned at commit `fcbd1076a93841fa88855acce810e342a5b78101` |
| Model | `omen-arc` — Qwen3-30B-A3B via `http://127.0.0.1:8082/v1` |
| Agent user | `hermes`, uid 1000, **not in the sudo group** |

### The fences, and why each one

`/etc/wsl.conf`:

```ini
[user]
default=hermes

[automount]
enabled=false      # no /mnt/c — the agent has terminal access, it does not get Derek's filesystem

[interop]
enabled=false      # Linux->Windows execution off
appendWindowsPath=false
```

- **`interop.enabled=false` is the load-bearing one.** Without it a shell in the
  distro can run `powershell.exe` and act as Derek on the host, which voids the
  whole sandbox. Windows→Linux is unaffected: `wsl -d hermes -- cmd` still works.
- **No sudo for the agent user** — with sudo it could `mount -t drvfs C: /mnt/c`
  and walk straight past `automount=false`. Provision as root **from outside**
  instead: `wsl -d hermes -u root -- …`. That split is the point: Derek can
  escalate, the agent cannot.
- **`HERMES_WRITE_SAFE_ROOT=/home/hermes`** in `~/.hermes/.env`.
- Approvals: `mode: smart`, `cron_mode: deny`, `single_query_mode: deny`.
- `security.allow_private_urls: false` (default) — blocks the agent's *web* tools
  from the LAN/tailnet. Does not affect the model or MCP paths.

⚠ **Mirrored networking means WSL2 is not a network boundary.** `.wslconfig` has
`networkingMode=mirrored`, so the distro sees OMEN's LAN, mshome and tailnet
addresses as its own. This is a filesystem and process boundary only. It is also
what makes the setup work at all — `127.0.0.1:8082` and `127.0.0.1:8710` are
reachable from inside with **zero changes to HEARTH** (`/mcp` returns 406, i.e.
it passes the ADR-0022 DNS-rebinding guard).

### Kill switch

```
wsl --terminate hermes
```

---

## Gotchas hit during the build (all real, all cost time)

1. **`OPENAI_API_KEY` will not work for a loopback endpoint.** Hermes host-gates
   provider env keys — `OPENAI_API_KEY` is only sent to `openai.com`/
   `openai.azure.com`, and `_host_derived_api_key()` returns empty for IPs and
   loopback (upstream #28660; the Ollama equivalent is GHSA-76xc-57q6-vm5m).
   This is a deliberate anti-credential-leak control, not a bug. For a local
   rung set **`model.api_key`** (`hermes config set model.api_key <token>`) or a
   `providers:` entry with `key_env:`.
2. **The installer stops at the C++ compiler check** if `build-essential` is
   missing and it cannot sudo — it exits without cloning anything. Install
   `build-essential` as root first.
3. **`--non-interactive` does not skip the setup wizard.** The installer still
   lands on the "How would you like to set up Hermes?" menu and blocks on stdin.
   Configure declaratively with `hermes config set` instead.
4. **`wsl.exe -d <d> -- bash -c '…'` does not receive stdin** (a bare exe like
   `tee` does, and `bash -s` heredocs do). Matters for handing secrets in
   without putting them in argv.
5. **Git Bash mangles WSL paths in argv** — `/home/hermes/x` becomes
   `C:/Program Files/Git/home/hermes/x`. Set `MSYS_NO_PATHCONV=1`.

---

## Measured behaviour on this rung

First cold one-shot: **57.9 s**. The same shape warm: **9.5 s**.

That gap is the prefix cache, and it is the single most important operating fact
here. Hermes sends a fixed ~13.9k tokens of system prompt + tool schemas on
*every* call (upstream #4379). On this rung that is **~25 s of prefill** before
any conversation content — unless the prefix is still cached, which measured a
**284x** reduction in prefill time.

Full depth curve in
`E:\work\battlemage\burnin-2026-08\results\expH-hermes-prefill-depth-20260824.md`.

**So the thing to watch is slot eviction, not raw speed.** With `-np 2` and
subagents in play, an evicted prefix costs the full 25 s again.

Multi-step tool use verified against ground truth (wrote a file, read it back,
checked on disk rather than trusting the model's self-report).

---

## The rung change this required

`omen-arc` served `-c 65536 -np 4` = **16,384 tokens per conversation**. Hermes
refuses any model offering under 64,000. Now `-c 131072 -np 2` = 64k × 2.

See `fleet/arcserve/serve-arc.cmd` for the measured spill data behind choosing 2
slots over 4, and `hearth/etc/backends.toml` for the matching `context_bytes`.
**Those two must move together** — llama-server silently truncates over-long
prompts rather than rejecting them, so `context_bytes` is the only real guard.

---

## What this is for

The open question is which of these Hermes makes unnecessary:
`hearth/callers/local_caller.py`, `agent_openai.py`, `hearth/commander/refine.py`,
the conductor + builder VMs, the IRC BotHerder, Watchfire's patrol loop — all of
which exist to "run an agent somewhere and collect the result", and all of which
are the *commodity* layer.

What Hermes does **not** touch: HEARTH's governance (caller → profile →
capability, the ledger, the authority domains), the knowledge/belief layer, the
measurement apparatus, and the routing economics. Building less mechnet means
building less plumbing, not less moat.

The way to answer it is to use this instance for real work and see which of those
stops being missed.

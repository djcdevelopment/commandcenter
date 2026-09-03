# omen-arc keep-alive — scheduled from fx99

Keeps the `omen-arc` rung out of the cold state characterised in
[ADR-0043](../../docs/adr/0043-the-rung-goes-cold-when-idle.md).

## Why it exists

| idle before the request | what the rung does |
|---|---|
| 0 / 30 / 60 s | 106.6 / 106.5 / 106.5 tok/s — no loss |
| **120 s** | **39.5 tok/s** — collapsed (replicated: 39.71 / 39.54) |
| 300 s | 28.7 tok/s, then a flat ~27.5 plateau |

And the **first** request after an idle gap pays a wall-clock stall of **~11.5 s**
regardless of size — measured at 10 735 ms to prefill *11 tokens*, and again at 11 540 ms
for a *1-token* ping. ⚠ That stall is largely **invisible to llama.cpp's own timings**: on
the 1-token ping the server reported `prompt_ms=47.1` and `eval_ms=0.0` while `launch_slot_`
→ `release` spanned 11.54 s. Anything that trusts `print_timing` will not see it.

A **1-token request every 20 s held the rate at 104.83 tok/s**, indistinguishable from a
freshly loaded server. Confirmed live at this timer's 30 s interval: **105.43 tok/s (99% of
baseline, 0.41% spread)** across ~6 minutes whose only traffic was the ping itself.

⚠ **Six minutes is the tested horizon, not a guarantee.** On 2026-08-30 an epoch ran ~35 minutes
with this timer running and degraded to 61% — a state a restart did **not** clear, so probably a
different phenomenon, but the keep-alive is not evidence against it. The **deep probe caught it**,
which is what it is for.

## ⚠ It must start from a WARM rung

Warming prevents the transition; it does **not** reverse it. Started against an already
collapsed rung, the pinger keeps prefill fast and decode stays broken — measured with
pings landing every 30 s and every receipt reading `prefill 44 ms, stall false`, while a
rate check still returned **42.54 tok/s (40%)** with the familiar 68.82 → 26.40 decay.

**So after any outage, restart the rung before relying on the keep-alive:**

```powershell
schtasks /Run /TN ArcServeRestart
```

The pinger never does this itself. It cannot fix a collapsed rung anyway, and a keep-alive
that reached for a restart would be an outage generator on a flaky link. Detection is what
the deep probe is for; the decision to restart stays with a human.

## Two timers, and why

| unit | every | tokens | job |
|---|---|---|---|
| `arc-keepalive.timer` | 30 s | 1 | keep the rung warm (~45 ms/tick) |
| `arc-keepalive-deep.timer` | 5 min | 32 | **measure decode**, then invoke fenced image-session recovery on OMEN |

The deep probe exists because **a 1-token ping cannot see the collapse it prevents** — it
generates no measurable decode. Without it the monitor would be unfalsifiable: every
receipt would read healthy whether or not the rung was.

## Why the schedule lives on fx99

A keep-alive that runs on the box it is keeping alive dies with that box. fx99 (`ai-1`) is
an always-on monitoring node that already carries fleet timers, so it owns the schedule;
OMEN owns the action and the secret.

- `fleet/arcserve/warm-arc.ps1` (on OMEN) issues the ping against `127.0.0.1:8082` and
  reads the bearer from OMEN's gitignored `gateway.cmd`. **fx99 never holds the token**,
  and the rung stays loopback-bound — SSH is the transport precisely so that nothing has
  to open `:8082` to the network.
- Every ping appends a receipt to `hearth/var/arc-keepalive.jsonl` on OMEN with the wall
  time and the server's own prefill/decode figures, so **the pinger doubles as the
  monitor**. That is what will finally answer whether real traffic has been running in the
  cold regime all along — the open question ADR-0043 could not settle.

Failure is quiet and non-actuating: if OMEN is unreachable the tick logs and exits. It
never restarts anything. A keep-alive that reached for a restart would be an outage
generator on a flaky link.

The deep timer also runs `recover-omen-imagegen.sh`. That is separate from the warm
probe's policy: FX99 merely invokes `E:\omen\imagegen\ops\Invoke-ImageGenRecovery.ps1`
over the same SSH transport. The OMEN-side recovery code acts only on an abandoned,
expired image-session fence with no claimed work or listening image backend; a healthy
session is a no-op. ArcServe credentials and the restart action remain on OMEN. Receipts
for this check live in `E:\omen\imagegen\data\logs\recovery.log` on OMEN and
`/var/log/imagegen-recovery.log` on FX99.

## Transport status — live over the tailnet, LAN still closed

**It works today.** fx99's `~/.ssh/id_omen` (created 2026-08-10, evidently for exactly
this) is already authorised on OMEN, so no key work is needed. ⚠ An earlier check
suggested otherwise — that test used fx99's *default* key rather than `-i id_omen`, and
read the resulting `Permission denied (publickey)` as "the key is not authorised". It was
the wrong key, not a missing grant. `warm-omen-arc.sh` always passes `-i`.

| path | status |
|---|---|
| LAN `192.168.12.239:22` | **blocked** — the `OpenSSH SSH Server (sshd)` rule is `Private`-profile only, and `Ethernet 3` is categorised **Public** |
| tailnet `100.124.12.37:22` | ✅ **working** — this is the live path |

⚠ **The live path crosses ADR-0014/0015**, which reserve Tailscale for humans and Funnel
and make the LAN the machine lane. It is running on the fallback, not the design. Opening
the LAN closes that gap; the script tries LAN first and will switch itself the moment it
is available. One narrowly scoped rule — one address, one port — is enough, and it is an
operator action because it is a firewall change:

```powershell
New-NetFirewallRule -DisplayName "OpenSSH from fx99 (arc keep-alive)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 22 -RemoteAddress 192.168.12.220 -Profile Any
```

That is tighter than re-categorising `Ethernet 3` as Private, which would expose every
Private-profile rule to the whole LAN.

⚠ **Cost of the fallback:** each tick wastes an 8 s `ConnectTimeout` failing over the LAN
before it reaches the tailnet, so a ping costs ~10 s of wall time instead of ~0.1 s. It
still keeps the rung warm — the interval is measured from tick start — but until the LAN
is open, `/var/log/arc-keepalive.log` carries one `FAILED via 192.168.12.239` line per
tick. That noise is deliberate: it is the reminder that the doctrine-preferred path is
still shut.

## Install (on fx99)

```bash
sudo ./install.sh
```

Idempotent. `--dry-run` to preview, `--disable` to stop and disable without removing
files. Verify:

```bash
sudo -u derek /opt/arc-keepalive/warm-omen-arc.sh; tail /var/log/arc-keepalive.log
```

## Checking it is doing its job

On OMEN, `hearth/var/arc-keepalive.jsonl` carries one row per tick. Two fields matter:

- **`prefill_stall: true`** — the keep-alive is not keeping up. The interval is too long,
  ticks are being missed, or the transport is failing.
- **`decode_degraded: true`** (deep-probe rows only) — the rung has already collapsed and
  the pinger **cannot** recover it. Restart it.

That is the whole point of logging the ping rather than firing and forgetting. ⚠ The file
is written BOM-less on purpose: PowerShell 5.1's `Add-Content -Encoding utf8` emits UTF-8
*with* a BOM, which makes the first line of a `.jsonl` fail to parse in any ordinary
reader — it broke this project's own reader the day it was written.

## Interval

30 s, from the measured band: 60 s idle is still clean and 120 s is collapsed, so 30 s
carries 2× margin to the first failing point at a cost of one ~50 ms request per tick.
`Persistent=true` is deliberately **not** set — a missed warm-up is worthless after the
fact, and catching up on a backlog would just fire a burst at a rung one ping already
warmed.

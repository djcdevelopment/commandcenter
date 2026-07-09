# ADR-0014 — Machine lanes ride local networks; Tailscale is for humans and the Funnel

**Status:** Accepted (2026-07-09) — implemented and verified live the same day.

## Context

On 2026-07-09 the HEARTH task lane went dark: SSH from OMEN to cc-conductor stalled on
a Tailscale SSH **check-mode** prompt ("Tailscale SSH requires an additional check —
visit https://login.tailscale.com/..."). The head of mechnet was blocked pending a
human logging into a browser. Two Tailscale mechanisms cause this class of outage, and
both are policy/config, not code: SSH ACL `action: check` (periodic browser re-auth by
design) and node key expiry (~180-day default).

The deeper mismatch: cc-conductor is a Hyper-V VM **on OMEN itself**, and AM4 sits on
the same home LAN (192.168.12.0/24). Every machine-to-machine lane was riding a cloud
control plane to reach hardware that is one virtual switch or one Ethernet hop away.
Tailscale was chosen originally because it was easy, not because the machine lanes
needed it. The fleet's own addressing model (2026-07-02, "VMs never join the tailnet")
already pointed this direction — cc-conductor was the lone exception, and AM4's lanes
had simply never been migrated.

Verified before deciding: key-based BatchMode SSH to `cc-conductor.mshome.net` works
over the Default Switch with no Tailscale involved; AM4's sshd answers on the LAN; the
oxen facade binds `0.0.0.0:8090` and only ufw blocked LAN access (rule added 2026-07-09,
matching the existing 8080-from-LAN pattern).

Alternatives considered and rejected:
- **Move conductor to AM4** — does not touch the auth problem; adds an always-on
  coordination process to the RAM-tightest, GPU-busiest box.
- **Move conductor to WSL** — does not touch the auth problem; loses the Hyper-V
  isolation boundary and checkpoints; WSL lifecycle is tied to the interactive session,
  which is the same always-on-posture failure mode as the Interactive-only scheduled
  tasks (see ADR-0015).
- **Fix only the Tailscale policy** (check→accept, disable key expiry) — still leaves
  the local control loop dependent on an external control plane's health and policy.

## Decision

1. **All HEARTH/mechnet machine-to-machine lanes use local addressing.** Hyper-V VMs
   via `*.mshome.net` sibling DNS (names, never raw 172.x IPs — the Default Switch
   subnet shifts across host reboots); physical AM4 via its static LAN address
   `192.168.12.233`. Concretely: the task lane and conductor git mirror dial
   `cc-conductor.mshome.net`; summon/occupancy/am4/dream and the `am4-oxen` backend
   endpoint dial `192.168.12.233`.
2. **Tailscale remains for humans and the Funnel only:** phone/i5 roaming access
   (another project) and the AM4 gallery Funnel. It is never in the mechnet control
   loop. Machines authenticate with SSH keys; browsers are for humans.
3. **cc-conductor stays a Hyper-V VM on OMEN** (decision B of the 2026-07-09 review):
   the isolation boundary, checkpoint/export provisioning, and boot-time autostart are
   earned capital that WSL/AM4 relocation would spend for no new capability.

## Consequences

- The conductor lane survives Tailscale outages/re-auths — verified live: the
  `task_status` round-trip succeeded over mshome **while** the tailnet route was still
  blocked pending browser re-auth.
- AM4's ufw now allows 8090/tcp from 192.168.12.0/24 (comment: "oxen facade from LAN -
  mechnet lane off tailnet"). Tailscale stays installed on AM4 for the Funnel.
- cc-conductor's tailnet membership is now unused by HEARTH/mechnet — candidate for
  `tailscale logout` once Derek confirms nothing human-facing (e.g. dashboard :8080
  from his phone) still rides it. Tracked in DECISIONS-PENDING.md.
- Remaining human lanes on the tailnet should get the policy hygiene fix anyway
  (disable key expiry on server nodes; SSH ACL check→accept) — Derek action, admin
  console. Tracked in DECISIONS-PENDING.md.
- known_hosts entries are keyed by the name dialed: mshome/LAN names were recorded
  fresh (2026-07-09). New lanes must remember `StrictHostKeyChecking=accept-new` on
  first contact or they fail in BatchMode.
- Docs and run artifacts referencing `100.74.110.91` / `100.116.82.60` /
  `am4.tail8e749c.ts.net` are historical; `fleet/inventory.toml` is the source of
  truth for how to reach a node (addressing model v2 in its header).

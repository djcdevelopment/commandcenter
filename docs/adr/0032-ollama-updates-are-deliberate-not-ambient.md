# 0032 — Ollama updates on OMEN are a deliberate act, not an ambient one

**Status:** Accepted and implemented (2026-08-19)

## Context

OMEN's Ollama install had two owners with incompatible assumptions:

- **OllamaBoot** (scheduled task, boot trigger, S4U logon) runs `ollama.exe serve`
  continuously — the fleet's inference rung behind the HEARTH door.
- **The tray app** (`ollama app.exe`, autostarted by a Startup-folder shortcut the
  installer creates) assumes it owns the lifecycle: it silently downloads updates to
  `%LOCALAPPDATA%\Ollama\updates_v2` and runs the Inno Setup installer with
  `/VERYSILENT /SUPPRESSMSGBOXES /FORCECLOSEAPPLICATIONS`.

The collision is deterministic. RestartManager cannot close the S4U-session serve from
the user-session updater ("Permission Denied+Session Mismatch" — verified in
`upgrade.log`, 2026-08-13 01:02), so replacing `ollama.exe` fails with `DeleteFile code
5`. Under `/SUPPRESSMSGBOXES`, Inno defaults the Abort/Retry/Ignore box to **Abort** and
**rolls back — and the rollback uninstalls `lib/ollama`**. The result is the worst kind
of broken: a zombie that answers `/api/tags` and `/api/version` (every liveness probe
green) while every generate returns HTTP 500 "llama-server binary not found".

Occurrences: 2026-07-17 (repair-install log), 2026-07-30 (~30 min silent outage; drove
the sentinel's serviceability checks), 2026-08-13 (upgrade.log above). On 2026-08-19 the
defect was caught mid-flight: a second updater (0.32.13→0.32.14) fired minutes after a
winget upgrade, with serve running.

Alternatives evaluated:

- **(b) Sentinel auto-repair** (re-run cached `OllamaSetup.exe` with serve stopped):
  treats the symptom, leaves the 1 AM ambient updater armed, and puts an installer
  inside a watchdog tick — a repair that can itself race the next ambient update. The
  sentinel stays a detector.
- **(c) Scheduled update window that stops OllamaBoot first**: still an unattended
  installer run on a box where an interactive session may be mid-inference; and version
  bumps on an inference rung are not urgent enough to automate. Kept as an *on-demand*
  script rather than a schedule.

## Decision

**1. The tray app no longer autostarts.** The Startup shortcut is parked in place
(`Ollama.lnk` → `Ollama.lnk.disabled`), which the Startup folder ignores. `ollama serve`
does not self-update, so with the tray gone there is **no ambient updater left**.
OllamaBoot is the sole owner of serve.

**2. OllamaBoot is hardened to carry that ownership** (elevated
`Set-ScheduledTask`, 2026-08-19): `ExecutionTimeLimit` PT72H → **PT0S** (the scheduler
was silently entitled to kill serve every 3 days), `RestartCount 3` @ 1 min (with the
tray gone, nothing else revives a crashed serve — the door's `start_ollama` is still a
stub), and battery settings neutralized.

**3. Updates go through `fleet/update_ollama.ps1` — the one sanctioned lane.** It
quiesces first (stop OllamaBoot, kill both process names, *wait for exit*), installs
via winget (or a passed `-SetupExe`), then undoes the installer's own re-arming — kills
the relaunched tray+serve and re-parks the Startup shortcut the installer re-creates —
restarts OllamaBoot, and refuses to report success until the sentinel's serviceability
probe passes a **real one-token generate**. `/api/version` answering is exactly what a
zombie also does; only a completion proves the update left a working runtime.

**4. The sentinel serviceability check stays as the tripwire** (unchanged), in case an
ambient updater ever returns (a reinstall outside the script re-creates the shortcut).

## Consequences

- The 1 AM zombie-install cannot recur through the tray lane: the process that launched
  those updates no longer runs. The residual paths (manual `winget upgrade`, running the
  installer by hand) fail the same way *only if* serve is up — the script exists so
  nobody has to remember the quiesce order.
- The tray GUI is still available by launching it manually; while it runs, its updater
  is armed again — acceptable for a deliberate, attended session.
- **Every installer run re-creates `Ollama.lnk`.** Updating outside
  `fleet/update_ollama.ps1` silently re-arms autostart; the script is the lane
  precisely because it re-parks it.
- Serve now survives crashes (3 restarts) and 72-hour uptimes. Task-settings changes
  require elevation (the task is registered `RunLevel HighestAvailable`); the one-shot
  script pattern used here (scratchpad script + UAC) is the precedent.
- Rollback is two reversible moves: rename `Ollama.lnk.disabled` back, and re-apply the
  old task settings.
- **Verification honesty:** parked shortcut, task settings (`PT0S`/3/`PT1M`), and a
  passing `--probe-generate` on `qwen3-coder:30b` under the hardened task were all
  verified live on 2026-08-19 against 0.32.14. The crash-restart path (RestartCount)
  has not been staged; it is scheduler-native behavior, not custom code.

# AM4 Valheim capture node — quick start

The workstation service, X configuration, and OMEN helper scripts are tracked in
this repo under `am4-fleet-node/` and `tools/ops/`. The Valheim-specific scripts
and canonical capture notes remain on AM4 under `~/valheim-capture/`.

AM4 runs the Valheim **client** on its RTX 5070 so the orbit / selfie-stick capture
mod can shoot frames. It is a capture node, not a second copy of the pipeline —
planning, scoring and gallery build stay on OMEN.

```bash
ssh homebase        # derek@192.168.12.233
```

---

## The two rules that govern everything

They sound contradictory. Both are true at once, and violating either produces a
failure that looks like something else entirely.

**1. Steam must be RUNNING.** Valheim's client calls `SteamAPI_Init`, which opens an
IPC pipe to a live Steam client. Without one you get a **black screen with a working
cursor and mod overlays still drawing** — which reads as a graphics fault and is not.
The tell is in `BepInEx/LogOutput.log`:

```
InvalidOperationException: Steamworks is not initialized.
  SceneLoader.Awake ()
[Error :Comfy Camera Proof] Orbit auto-boot: no FejdStartup.
```

**2. The game must NOT be launched THROUGH Steam.** Unix doorstop frequently fails to
inject when Steam starts the process (Valve runtime #747), so BepInEx plugins silently
do not load — and an unattended runner cannot notice. Always launch with
`start_game_bepinex.sh`. Never use Steam's Play button.

So: **Steam logged in and idle in the background; game started by script.** Both.

`~/.steam/sdk64/steamclient.so` (symlink to steamcmd's `linux64/`) is **necessary but
not sufficient**. Without it: `dlopen failed`. With it but no client:
`[S_API FAIL] SteamAPI_Init() failed; create pipe failed.` Keep it; it does not
replace the client.

---

## Start a session

```bash
~/valheim-capture/start-steam-session.sh          # Steam on :0; Openbox is already managed
cd ~/valheim && DISPLAY=:0 ./start_game_bepinex.sh -console &
```

Steam account is **`waryfool`** (persona **Zephar410**). Derek owns 3 Valheim
licenses on 3 accounts specifically so AM4 and OMEN can both run Valheim at the same
time — AM4 takes `waryfool`, OMEN uses another. **Do not sign AM4 into whichever
account OMEN is using**; the second launch kicks the first out of the game.

## Display: two modes, one switcher

```bash
sudo valheim-display status         # which mode, is X up, what outputs
sudo valheim-display monitor        # ASUS MG28U on DP-0, 3840x2160 at 60 Hz
sudo valheim-display headless       # UseDisplayDevice None, Virtual 3840x2160
```

`/etc/X11/xorg.conf` is only a **symlink** to `xorg.monitor.conf` or
`xorg.headless.conf`. Never edit it in place.

⚠ **Switching modes restarts `valheim-xorg`, which kills everything on `:0` —
including Openbox, Steam, and Valheim.** Monitor mode automatically brings back
managed Openbox and Deskflow; Steam and Valheim stay manual. Choose the mode
*first*, then start Steam:

```
sudo valheim-display headless  →  start-steam-session.sh  →  run-capture.sh
```

## OMEN keyboard, mouse, and VNC

Deskflow makes the physical AM4 panel the screen immediately to the **right** of
OMEN. Push the pointer through OMEN's outer-right edge to enter AM4 and through
AM4's left edge to return. Clipboard sharing is enabled up to 64 MiB.

- **Scroll Lock** toggles cursor lock on the current computer. While locked on
  AM4, Deskflow uses relative mouse movement, which is the useful mode for
  Valheim camera control.
- OMEN's server listens only on its Tailscale address `100.124.12.37:24800`.
  Both peer certificates are pinned; port 24800 is not exposed on OMEN's LAN IP.
- OMEN uses Deskflow's portable build and a per-user Startup shortcut, so the
  server starts when Derek signs into Windows. It is not available on the Windows
  sign-in or secure UAC desktop; use OMEN's physical input there.
- `am4-workstation.target` starts Openbox and the Deskflow client at boot only
  when `/etc/X11/xorg.conf` points at `xorg.monitor.conf`. Headless mode stops
  both so a capture cannot be disturbed by an accidental edge crossing.
- Monitor mode zeroes X screen-blanking and DPMS timers. The ASUS otherwise
  enters DisplayPort deep sleep after ten idle minutes and appears to lose signal.

Deskflow shares input and the clipboard, not pixels. For recovery or for seeing
the desktop in a window on OMEN, launch the on-demand VNC helper from the repo:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\ops\am4-vnc.ps1
```

It starts `x11vnc` only for the lifetime of an SSH tunnel, binds the VNC server
to AM4 loopback, and opens TigerVNC Viewer on OMEN. It adds no firewall rule and
stores no VNC password because the SSH tunnel is the authentication boundary.

## AM4 files from Windows

Explorer drive `A:` maps to `\\192.168.12.233\AM4`, AM4's `/home/derek`, over
encrypted SMB3 on the private home machine lane. Samba listens only on loopback
and AM4's fixed Wi-Fi address and accepts only OMEN (`192.168.12.239`). The
mapping is persistent and Windows Credential Manager holds the randomly
generated Samba password.

SSH keys and common credential stores are deliberately vetoed from the share,
including `.ssh`, `.gnupg`, `.pki`, `.aws`, `.azure`, `.kube`, `.docker`,
`keyrings`, and the Deskflow and Moonlight certificate directories. Symlinks
are not followed.

Recovery checks:

```powershell
& .\tools\ops\map-am4-drive.ps1
Get-SmbMapping -LocalPath A:
Test-NetConnection 192.168.12.233 -Port 445
```

Use `-ResetCredential` only when rotating the Samba password; it generates the
replacement in memory, updates AM4 over SSH, and saves the Windows credential.

```bash
ssh homebase 'sudo systemctl status smbd --no-pager'
ssh homebase "sudo ss -lntp '( sport = :445 )'"
```

`run-capture.sh` defaults to 3840x2160 and preflights the GL renderer but not the
screen size. **This does NOT clamp** — measured 2026-08-27, a capture taken while X
was in `monitor` mode with a 1920x1080 `Virtual` still produced **3840x2160** PNGs.
The mod renders to an offscreen target sized independently of the X screen, so
capture resolution is not bounded by the display mode. Prefer `headless` for
unattended runs because it needs no panel and nothing on screen can disturb it —
not for resolution.

## Unattended capture

```bash
~/valheim-capture/run-capture.sh --plan ~/valheim/BepInEx/config/shotplan.tsv
```

It refuses to start if Valheim is already running (the DLL and plan are read at
startup). It arms `orbit-request.json`, installs the plan, parks the NetworkSense
server auto-join, hides the HUD and quest creator bar, and restores every config on
all exit paths. Receipts land in `BepInEx/config/shotplan-receipts.jsonl`.

`orbit-request.json` auto-boots the mod into a world/character at startup. **Park it
before any interactive session** or it hijacks the menu:

```bash
mv ~/valheim/BepInEx/config/orbit-request.json{,.parked}
```

(The rig already has a convention for this — you will see `.disarmed`,
`.paused-for-questlab-*` siblings. Restore whichever you parked.)

## Verify, don't assume

```bash
sudo valheim-display status
pgrep -x steam && echo "steam up"
grep -ac "Steamworks is not initialized" ~/valheim/BepInEx/LogOutput.log   # want 0
grep -a "S_API" ~/valheim/valheim-stdout.log                              # want no "create pipe failed"
grep -aE "Loading \[" ~/valheim/BepInEx/LogOutput.log                     # want 6 plugins
```

**Plugins loading does NOT mean the game started.** BepInEx plugins load and draw
overlays even when the game never reaches its main menu. That mistake was made in
this build-out and cost real time. Confirm `FejdStartup` and an actual menu, or a
world that renders — not a log line.

Same rule for captures: a `orbit capture finished: N/N` log line is not proof.
Check the PNGs exist, and check their dimensions:

```bash
ls -la ~/valheim/BepInEx/config/comfy-orbit-captures/<run-id>/
wc -l < ~/valheim/BepInEx/config/shotplan-receipts.jsonl
```

**First end-to-end capture proven 2026-08-27** — run `20260827-084611`, 4/4 shots,
four 3840x2160 PNGs (4.1–6.1 MB each) and 4 receipt lines, with X in `monitor` mode.
Receipts record planned vs placed vs lens position, `lens_offset_m`, aim, yaw, pitch
and clearance per shot.

## Known-benign noise

| Message | Verdict |
|---|---|
| `DllNotFoundException: party` | PlayFab crossplay native lib, no Linux build. Capture plays a local world; irrelevant. |
| `PlayFab login request (attempt 1)` + stack trace | Followed by `Logged in PlayFab user via Steam auth session ticket` — it succeeded. |
| `ALSA lib pcm_dmix … unable to open slave` | No audio device. Harmless. |
| `PosixFileOpen: RESOLVE_BENEATH unsupported` | 32-bit steamcmd on a 7.x kernel. Harmless. |
| GTK dialogs appearing as unclickable 1x1 windows | xdg-desktop-portal activation stall. `XDG_CURRENT_DESKTOP` is now set in the session script; if you see it again, wait out the 120 s timeout rather than assuming a hang. |

## Host facts worth knowing

AM4 was headless its entire life, so several things normal desktops have were simply
absent and had to be added. All permanent now:

- `xserver-xorg-input-libinput` — X had **no input driver at all**; every device
  logged `No input driver specified, ignoring this device`, so keyboard and mouse
  were dead in X while working fine at the kernel level.
- `libnvidia-gl-595:i386` — the Steam client is 32-bit and needs 32-bit GL. Version
  must match the loaded kernel module (595.84); a skew breaks GL.
- `openbox` — bare WM so dialogs can take focus. There is no desktop environment and
  none is needed.
- `deskflow` — encrypted keyboard, mouse, and clipboard client for the physical
  workstation mode.
- `x11vnc` and `xclip` — on-demand SSH-tunneled screen recovery and X11 clipboard
  support.

There is **no display manager**. `valheim-xorg.service` owns X, while
`am4-workstation.target` owns Openbox and Deskflow in physical-monitor mode.

## Do not

- **Do not rebuild the `josh5/steam-headless` container.** Deleted 2026-08-27 — image
  removed and `docker-compose.yml` renamed to `docker-compose.yml.retired-20260827`.
  It existed only to supply a running Steam client, which native Steam now does, and
  its GPU wiring was stale anyway: `NVIDIA_VISIBLE_DEVICES: "void"` plus `/dev/dri`
  was written for the Arc B70s that left in the 2026-08-20 rebuild. Reclaimed 7 GB.
  Note a container image is also the wrong shape for this job — it would need GPU
  passthrough that the retired compose explicitly disabled.
- **Do not confuse the dedicated server with the client.** `server-compose.yml`
  (`ghcr.io/community-valheim-tools/valheim-server`) uses **anonymous** steamcmd and
  never needed an account. The client does. That server side is **live and holds
  7.6 GB of world state** in `~/comfy-valheim-lab/server-state/` — do not clean it up
  alongside client leftovers. `state/client-shared/` (288 KB) also survives: it holds
  a `teleport-route.tsv` that may exist nowhere else.
- **Do not `pkill -f valheim.x86_64`** over SSH. The pattern matches your own command
  line and kills the shell running it — silently, mid-script. Use `pkill -x`.

## Reinstalling the game

```bash
steamcmd +@sSteamCmdForcePlatformType linux \
         +force_install_dir /home/derek/valheim \
         +login waryfool +app_update 892970 validate +quit
```

The platform override and `force_install_dir` **must** precede `+login`. Then
`~/valheim-capture/install-mods.sh` to lay OMEN's exact BepInEx tree on top.

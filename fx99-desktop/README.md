# FX99 virtual workstation on AM4's monitor

FX99 is physically headless. `fx99-xorg` makes the NVIDIA driver expose a fake
DP-0 and creates a GPU-backed 3840x2160 X11 screen on the RTX 2070 SUPER;
`fx99-desktop` adds a minimal Openbox workspace,
and `fx99-sunshine` sends it to Moonlight on AM4. Sunshine is bound to FX99's
LAN address, requires encrypted streams, disables UPnP and audio, and uses NVENC
so the resident Ollama model is not displaced.

The physical path is:

```text
OMEN keyboard/mouse -> Deskflow -> AM4 -> Moonlight -> Sunshine -> FX99 Xorg
FX99 pixels -> Sunshine/NVENC -> Moonlight -> AM4's DisplayPort monitor
```

AM4's Wi-Fi is asymmetric. The relevant FX99-to-AM4 direction measured about
50 Mbit/s on 2026-08-28, so Sunshine is capped at 40 Mbit/s and the AM4 launcher
requests 35 Mbit/s HEVC. The opposite direction measured only about 8 Mbit/s;
that does not carry the video.

```bash
ssh fx99 'sudo systemctl status fx99-xorg fx99-desktop fx99-sunshine --no-pager'
ssh fx99 'tail -n 100 ~/.local/state/fx99-sunshine.log'
ssh homebase 'sudo fx99-display show'
ssh homebase 'sudo fx99-display hide'
```

Inside Moonlight, `Ctrl+Alt+Shift+Q` returns to AM4, `Z` toggles input capture,
`X` toggles fullscreen, `S` shows latency statistics, and `M` toggles between
direct-pointer and captured-pointer mouse modes (all with
`Ctrl+Alt+Shift` held).

Sunshine's web UI is reachable only on FX99's LAN-bound listener and still
requires its rotated random credential. Tunnel it when needed:

```powershell
ssh -L 47990:192.168.12.220:47990 fx99
# then open https://localhost:47990
```

`rotate-web-credential.sh` deliberately does not print or save the generated
plaintext. The Web UI can be reset again over SSH whenever it is needed.

The stable Sunshine package is `v2026.516.143833` for Ubuntu 24.04, verified at
install time against SHA-256
`6df8900f23c9c056252eea51639507b8239a1d1241308ab8923cb402b0ca653b`.

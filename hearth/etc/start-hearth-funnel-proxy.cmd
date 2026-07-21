@echo off
rem Caddy reverse proxy in front of HEARTH's gateway, for Tailscale Funnel to
rem target instead of the bare gateway port (Track 2.0 minimal ADK test case).
rem Only forwards /mcp; everything else 404s (see hearth\etc\caddy\Caddyfile).
rem `tailscale funnel 8711` should point at THIS process's port, never at 8710.
cd /d C:\work\commandcenter
"%LOCALAPPDATA%\Microsoft\WinGet\Packages\CaddyServer.Caddy_Microsoft.Winget.Source_8wekyb3d8bbwe\caddy.exe" run --config hearth\etc\caddy\Caddyfile --adapter caddyfile >> hearth\var\caddy-funnel-proxy.log 2>&1

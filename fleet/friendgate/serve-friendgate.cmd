@echo off
rem serve-friendgate.cmd -- the friend gate: an OpenAI-compatible mouth for an invited friend's own agent.
rem
rem   friend's harness -> Funnel (omen.tail8e749c.ts.net:443) -> Caddy :8711 (handle /v1/*)
rem                    -> THIS (127.0.0.1:8791, hearth.friendgate) -> llama-swap 127.0.0.1:8081/v1
rem
rem Run by the HearthFriendGateBoot scheduled task (S4U, boot trigger, ExecutionTimeLimit PT0S,
rem RestartCount 3 @ PT1M -- the same ADR-0032 hardening pattern as HearthFunnelProxyBoot).
rem
rem The rung's bearer (OMEN_ARC_TOKEN) is needed to speak to llama-swap and lives ONLY in the
rem gitignored hearth\var\gateway.cmd, so this launcher runs the gate THROUGH
rem hearth\etc\with-gateway-env.cmd -- the one sanctioned way to source it (CALLed silently, never
rem echoed, never redirected anywhere but nul). Started without it the gate fails CLOSED: every
rem completion answers 503 gate_unarmed (proved by hearth/tests/friendgate/test_gate.py).
rem
rem Per-friend keys: hearth\var\friendgate\keys.json (sha256 only), minted with
rem   fleet-worker-node\.venv-omen\Scripts\python.exe -m hearth.friendgate.friendctl mint --account <ergo account>
rem Usage rows: hearth\var\friendgate\usage.ndjson (one per request, principal irc_account:<friend>).

set PYTHONPATH=C:\work\commandcenter
set PYTHONUTF8=1
if not exist "C:\work\commandcenter\hearth\var\friendgate" mkdir "C:\work\commandcenter\hearth\var\friendgate"

call "C:\work\commandcenter\hearth\etc\with-gateway-env.cmd" ^
  "C:\work\commandcenter\fleet-worker-node\.venv-omen\Scripts\python.exe" -m hearth.friendgate ^
  > "C:\work\commandcenter\hearth\var\friendgate\task.log" 2>&1

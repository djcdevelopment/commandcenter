# Frontier callers — connecting to HEARTH

The gateway lives on OMEN (decided). Local callers — and frontier sessions
running on OMEN itself — reach it at localhost; callers on other boxes use
OMEN's tailnet address on the same port.

A frontier session (Claude Code, Codex pour) connects to the gateway as an
MCP streamable-http server. Add the stanza from `mcp-config-snippet.json`
to the project `.mcp.json` (or `claude mcp add --transport http hearth
http://127.0.0.1:8710/mcp --header "X-Hearth-Key: <key>"`).

- Endpoint: `http://127.0.0.1:8710/mcp` (streamable-http; localhost on OMEN).
- Auth: HTTP header `X-Hearth-Key: <caller key>`. Keys live in
  `hearth/var/callers.json` (gitignored); `dev-local` is the dev/test key. Unknown keys
  are rejected and the rejection itself is a ledger event.
- Identity: the key maps to `{id, runner_class, node}` — a frontier session
  is `runner_class: "frontier"`. Every tool call lands in
  `hearth/var/ledger/events.ndjson` with that provenance.

## The rule

**Frontier sessions are MCP-only: they act on the lab ONLY through the
gateway tools.** No SSH writes, no direct edits of `knowledge/*.json`, no
out-of-band `git push` to lab stores. If a needed tool is missing, that is a
tool-surface gap — log it (request_tool / BUILD-NOTES), do not route around
the gateway.

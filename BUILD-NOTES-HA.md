# BUILD-NOTES-HA — HEARTH Stream H-A (Kernel)

Built 2026-07-03 on branch `worktree-agent-a6daae8ab7a3e4775` against master @ fdb68dd.
Implements the L0 kernel per HEARTH-BUILD-ORCHESTRATION.html frozen contracts 1, 3, 4, 5:
gateway daemon, append-only ledger, caller auth, provenance wrapper, guard hooks,
kernel_change ceremony.

Placement note (Derek, post-launch): the gateway definitively lives on OMEN; frontier
callers are MCP-only with no general SSH write access; break-glass is operator policy
only — deliberately NOT implemented as a mechanism. The kernel itself stays
host-agnostic (no OMEN-specific paths or assumptions).

## What was built

| File | Role |
|---|---|
| `hearth/contracts/hearth-event.v1.schema.json` | Frozen event schema (JSON Schema 2020-12, `additionalProperties: false`) |
| `hearth/kernel/ledger.py` | `Ledger.append(event) -> event_id` / `Ledger.query(caller, tool, since, ok)`; NDJSON record + SQLite index; NO update/delete API; also `new_event(...)` (stamps digests/preview/ts/uuid) and `validate_event(...)` |
| `hearth/kernel/auth.py` | `AuthRegistry.resolve(key) -> Caller \| None`; loads `hearth/etc/callers.json`; unknown/missing key => None AND a ledger event (`tool="__auth__", ok=false`) carrying only a sha256 fingerprint of the key, never the key |
| `hearth/kernel/context.py` | `HearthContext(repo_root, ledger, caller)` dataclass shared by gateway + built-in tools |
| `hearth/kernel/guards.py` | `GuardStack.check(tool, args)`: refuses any tool touching `knowledge/` paths unless registered as a knowledge tool; reuses `tools/workflow/corpus_guard.check_fixture_taint` (imported, not rewritten) for fixture-taint on guarded tools; raises `GuardRejection` with messages starting `guard:` |
| `hearth/kernel/gateway.py` | The daemon: FastMCP streamable-http on 127.0.0.1:8710, provider discovery, provenance wrapper, built-in provider (`kernel_status`, `kernel_change`) |
| `hearth/etc/callers.json` | Ships one key: `dev-local` -> `{id: dev-local, runner_class: human, node: omen}` |
| `hearth/.gitignore` | Ignores `var/` (ledger + kernel snapshots never committed) |
| `hearth/tests/kernel/` | 29 unittest tests incl. 2 live end-to-end HTTP integration tests |

## How to run the gateway

```
cd C:\work\commandcenter
C:\work\commandcenter\fleet-worker-node\.venv-omen\Scripts\python.exe -m hearth.kernel.gateway --providers hearth.toolsurface.fs,hearth.toolsurface.git
```

- Endpoint: `http://127.0.0.1:8710/mcp` (override `--host/--port`).
- `--providers` is a comma list of importable modules exposing `get_tools() -> list[Callable]`.
  Missing/malformed modules are logged and SKIPPED, so the kernel runs standalone before
  H-B lands (built-in tools only).
- `--callers <path>` overrides `hearth/etc/callers.json`; `--ledger-dir <path>` overrides
  the ledger location; env `HEARTH_ROOT` moves the whole var tree (ledger =
  `$HEARTH_ROOT/var/ledger`, snapshots = `$HEARTH_ROOT/var/kernel_snapshots`; default
  `HEARTH_ROOT` = `<repo>/hearth`).

## How auth works

Callers put their key in the `X-Hearth-Key` HTTP header on every request. The wrapper
resolves it through `AuthRegistry` (from `callers.json`) per call. Unknown or missing
key => the tool call raises `PermissionError` (surfaces to the MCP client as a tool
error) AND the rejection is appended to the ledger as
`{tool: "__auth__", ok: false, caller: {id: "__unauthenticated__", ...}}` with a 16-hex
sha256 fingerprint of the presented key in `error`. Raw keys never touch the ledger.

## Header extraction mechanism (mcp 1.28.1 — for the integration session)

The streamable-http transport stores the **starlette `Request`** in
`ServerMessageMetadata.request_context` (see `mcp/server/streamable_http.py` ~L268),
which the lowlevel server sets as `RequestContext.request`. Inside a tool call it is
reachable via:

```python
request = fastmcp_instance.get_context().request_context.request  # starlette Request
key = request.headers.get("X-Hearth-Key")
```

`get_context()` raises `ValueError` outside a request — the gateway's key provider
returns `None` then (=> auth rejection). There is no `get_http_request()` helper in
1.28.1; the `Context.request_context.request` path is the supported route. Sync tools
execute on the event-loop thread, so the request contextvar is directly accessible.

Client side (1.28.1): `streamablehttp_client(url, headers=...)` is deprecated; the
current API is `streamable_http_client(url, http_client=httpx.AsyncClient(headers=...))` —
headers travel on a caller-provided httpx client (see `hearth/tests/kernel/test_gateway_http.py`).

## Provenance wrapper (gateway.make_wrapper)

Per call: resolve caller -> reject unknown -> set `HearthContext.caller` -> guards ->
time the call -> `sha256:` digests of canonical JSON (sort_keys, default=str) of args
and result -> append hearth-event.v1 -> return result unchanged. Provider exceptions
are logged (`ok=false, error="<Type>: <msg>"`) and re-raised. Guard rejections are
logged with `error` starting `guard:`. The wrapper preserves the provider's name,
docstring, and signature (string annotations resolved in the provider's own module) so
FastMCP builds correct tool schemas.

## kernel_change ceremony

`kernel_change(description, diff_path)` zips the entire `hearth/kernel/` source dir to
`$HEARTH_ROOT/var/kernel_snapshots/kernel-<utc>-<id8>.zip` and appends a dedicated
ledger event (`tool="kernel_change.snapshot"`) BEFORE acknowledging; the wrapper then
logs the call itself as a second event (`tool="kernel_change"`).

## Guards / knowledge tools registration

Tools from any provider module whose name ends in `.knowledge` (i.e.
`hearth.toolsurface.knowledge` from H-B) are auto-registered as the legitimate
knowledge writers. Every other tool that references a path resolving under the repo's
`knowledge/` dir is refused (`guard:` ledger event). Fixture-taint reuses
`corpus_guard.check_fixture_taint` verbatim with `repo_root` injected.

## Test results

- Hearth kernel: **29/29 OK** with the venv python
  (`& C:\work\commandcenter\fleet-worker-node\.venv-omen\Scripts\python.exe -m unittest discover -s hearth/tests`),
  including 2 integration tests that boot a real gateway subprocess on a random port and
  make authenticated + bad-key calls end to end over streamable-http (self-skipping if
  the port never binds).
- Existing repo suite: **177/177 OK** on system python (`python -m unittest discover -s tests`) — untouched.

## Contract deviations / notes for integration

1. **Auth-rejection caller identity**: the schema's `runner_class` enum has no "unknown"
   value, so rejection events use `{id: "__unauthenticated__", runner_class: "human",
   node: <gateway hostname>}`. If integration prefers an enum extension, that is a
   schema change (H-A owns the file).
2. **`HEARTH_ROOT` semantics**: interpreted as the hearth data root (ledger at
   `$HEARTH_ROOT/var/ledger`), not the ledger dir itself; `--ledger-dir` exists for a
   direct override.
3. **Windows/sqlite**: connections are opened-and-closed per operation (sqlite3's
   context manager does not close, which locks `index.sqlite` on Windows). Relevant if
   H-C reads the index concurrently.
4. **Duplicate tool names** across providers: first registration wins, later duplicates
   are logged and skipped.
5. **Two events per kernel_change** (ceremony + wrapped call) — by design, noted so the
   projection adapter doesn't double-count.
6. Empty `hearth/__init__.py` and `hearth/tests/__init__.py` created (shared-by-
   convention with H-B/H-C; identical empty files merge cleanly).

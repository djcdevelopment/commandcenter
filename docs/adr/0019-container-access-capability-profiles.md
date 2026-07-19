# 0019 — Container access is capability-profiled: explicit non-loopback bind, profile-gated tool surface

**Status:** Proposed (2026-07-18) — supersedes nothing; extends
[0005](0005-one-boundary-three-planes.md) (one boundary, three planes) and
[0014](0014-machine-lanes-off-the-tailnet.md) (machine lanes ride local networks).

## Context

Dockerized clients — Open Notebook first, other local workers after — need to reach HEARTH.
Docker Desktop containers cannot reach a host service bound to host loopback, and the gateway
binds `127.0.0.1:8710` (`hearth/kernel/gateway.py:50-51`); the startup script never passes
`--host` (`hearth/etc/start-hearth-gateway.cmd:15`). The intended container endpoint is
`http://host.docker.internal:8710/mcp`.

The naive fix — flip the default to `0.0.0.0` — is refused here, because an inspection of the
current wiring turned up a fact that makes it actively dangerous:

**HEARTH authenticates but does not authorize.** `AuthRegistry.resolve()` maps an
`X-Hearth-Key` to a `Caller` (`hearth/kernel/auth.py:65-82`), and the gateway wrapper uses that
caller *only* for ledger attribution (`gateway.py:190-195`, `243-249`). Neither `caller.id` nor
`caller.runner_class` is consulted for access control anywhere in the kernel or tool surface.
Every valid key therefore gets the entire mounted surface — 47 tools across 16 providers
(verified live via `kernel_status`). A container caller minted under today's model could:

- `read_file("hearth/var/callers.json")` — exfiltrate **every caller key**, including
  `claude-frontier`'s. `fs.py` has no denylist and the registry sits inside `HEARTH_SCOPE`
  (`C:\work\commandcenter;C:\work`, set at `start-hearth-gateway.cmd:7`).
- `read_file("fleet/inventory.toml")` — read the fleet map.
- `submit_task(...)` — command the SSH dispatch lane (`task_lane.py:32-35`, `_run_ssh`), which
  shells `ssh claude@cc-conductor.mshome.net` **as the gateway host's user, with the host's SSH
  identity**.
- `git_commit_push(...)`, `write_file(...)` anywhere under `C:\work`, `run_tests()` (subprocess),
  `wake_am4()` (`summon.py:29`, `ssh derek@192.168.12.233`).

Loopback-only binding is, today, the entire containment story. Removing it without adding
authorization would be strictly worse than the status quo.

A second, independent defect: `doorcheck` collapses door health and backend health into one
verdict — `report["ok"] = gateway up AND default_backend_up AND toolsurface ok`
(`hearth/callers/doorcheck.py:460-463`, exit code at `:506`). A cold Ollama therefore reports
the *door* as DEGRADED. This has already cost debugging time by making a downstream timeout
look like a HEARTH connectivity failure. The parts are computed separately (`:444`, `:454`);
only the verdict conflates them.

## Decision

**1. Loopback stays the default; non-loopback is an explicit, refusing-not-falling-back mode.**
`DEFAULT_HOST` remains `127.0.0.1`. Binding a non-loopback interface requires
`HEARTH_CONTAINER_ACCESS_ENABLED=1` (or `--allow-non-loopback`) *in addition to* a host setting.
A non-loopback host without the enable flag is a **startup error with a non-zero exit** — never
a silent fall back to loopback, because a gateway that quietly binds narrower than asked looks
identical to a container networking fault. The effective bind address is logged at INFO on every
start, and a non-loopback bind emits a prominent multi-line warning naming the exposure.

**2. Authorization is capability-profiled, not caller-specific.** The container is not denied
*because it is Open Notebook*; it is granted the **Research Agent** profile. The indirection is:

```
caller → profile → capability set → tool routing
```

Caller records gain optional fields — a profile, plus one path grant per enforced authority
domain (see §4):

```json
{ "id": "docker-open-notebook", "runner_class": "local", "node": "omen",
  "profile": "research",
  "file_scope":  ["C:\\work\\commandcenter\\docs"],
  "repo_access": ["C:\\work\\commandcenter"] }
```

- **Taxonomy is code.** `hearth/kernel/capabilities.py` holds `TOOL_CAPABILITY: dict[str, str]`,
  mapping every registered tool to exactly one capability — the same shape and precedent as
  `TOOL_CLASS` (`gateway.py:72`). A test asserts **every mounted tool has a mapping**, so adding
  a tool forces an explicit policy decision rather than silently landing in some profile.
- **Policy is config.** `hearth/etc/profiles.toml` (checked into git — policy should be
  auditable; only the key registry is secret) defines profiles and their capabilities, with
  `inherits` for composition.

v1 ships one assigned profile. `research` = `read` + `query` + `generate` + `status` +
`repo_metadata`: 13 of 47 tools. `builder` (adds `dispatch`, `test`, `write`, `repo_content`,
`repo_write`) and `orchestrator` (adds `queue`, `schedule`, `harvest`) are defined as the
intended growth path but **no caller is assigned them in v1**. The roles named as the longer
arc — Documentation, Reviewer, Fleet Orchestrator, Human Operator — slot in as further
`[profile.*]` entries without touching the kernel.

**3. Unknown tools fail closed for profiled callers.** A profiled caller invoking a tool with no
capability mapping is denied. A caller with **no** `profile` keeps the full surface — required
by the no-compatibility-break constraint, and what keeps `claude-frontier`, `omen-worker-1`, and
`dev-local` working unchanged. The minting CLI has **no default profile** (it is a required
argument), and `doorcheck` reports profile-less callers as an advisory, so the permissive path
cannot be reached by forgetting a flag.

**4. Permissions are modelled per AUTHORITY DOMAIN, not by forcing everything to be a path.**
Each capability belongs to exactly one authority domain, and the domain determines *how* it is
enforced:

| Domain | Resource model | Caller grant | Capabilities |
|---|---|---|---|
| `filesystem` | path containment | `file_scope` | `read`, `write` |
| `repository` | named repository | `repo_access` | `repo_metadata`, `repo_content`, `repo_write` |
| `gateway` | none (capability gate only) | — | everything else |

This replaces an earlier draft that proposed a second path-shaped `repo_scope`. That was a
category error: git tools legitimately need the *repository root*, which is precisely the
ancestor a narrowed `file_scope` exists to deny, so the two could never be reconciled inside
one path model. A repository is a **named resource**, not an ancestor path. Filesystem
authority does not imply repository authority or vice versa — `git.py:_repo_path` resolves
through `resolve_repo` (repository authority), while `fs.py` resolves through
`resolve_in_scope` (filesystem authority), and each reads its own contextvar.

The payoff is that new surfaces — container runtime, k8s, database, cloud API — get their own
domain with their own resource model instead of being crowbarred into path containment.
HEARTH already has non-path surfaces (`submit_task` is an SSH lane, `local_generate` an
inference endpoint) that were never going to fit.

Both path grants are validated at load to be **contained by** an existing `HEARTH_SCOPE` root;
one that escapes is a startup error. Narrowing only, never widening. Enforcement is a
`contextvar` set and reset around each call (verified: set/reset inside a synchronous frame
isolates correctly across threads). This keeps `hearth/var/` (keys, ledger) and `fleet/`
(inventory) unreadable *by construction* rather than by denylist — a denylist fails open the
day a new secret lands somewhere unlisted.

**4a. Authority domains are separate but NOT independent, and the loader enforces that.**
A content-bearing repository capability can *launder* around a filesystem narrowing:
`git_diff` renders the contents of any changed file in a repo, including files `file_scope`
denies. Granting both is incoherent, not merely wide. `assert_authority_coherence` therefore
refuses — at gateway startup *and* at `callerctl mint`/`rotate` time — any caller whose
profile holds a content-bearing capability on a repository extending beyond its `file_scope`.
Leaving that to whoever edits the policy to notice would be exactly the by-convention
containment this ADR exists to replace.

Consequence: `research` holds `repo_metadata` (`git_status`, `git_log` — branch, changed
paths, commit sha/author/date/subject, verified to carry no blobs at `git.py:83`) but **not**
`repo_content` (`git_diff`). A research agent gets repository history and working-tree state
without a path around its own file scope.

**5. Mechnet stays behind HEARTH, and containers get no fleet credentials.** `submit_task`
already dispatches with the *gateway host's* SSH identity — a container commands the lane
without ever holding a credential, and every call is ledgered. That property is the whole
argument for routing container access through the existing door rather than granting direct
fleet reach: SSH keys, `inventory.toml`, and worker shells stay host-side, and there is no
unledgered path. v1 withholds dispatch from `research` anyway — a notebook helps a human reason;
it does not queue fleet work.

**6. Health separates into independent facets.** `doorcheck` reports `gateway`, `auth`,
`tool_registry`, `mechnet`, `local_backends`, and `external_backends` separately. The top-level
verdict and exit code become **door-only** (`gateway ∧ auth ∧ tool_registry`); backend
degradation is reported as an advisory, with `--strict` restoring the old all-inclusive verdict.
A cold inference backend must never read as "HEARTH unavailable." An unauthenticated
`GET /healthz` liveness route is added via FastMCP's `custom_route` (verified present in the
installed SDK) returning a static payload with no fleet detail — Compose healthchecks need a
probe that costs nothing and leaks nothing.

**7. Firewall scoping is documented, never automatic.** No rule is created without an explicit
request. `0.0.0.0` is acceptable *only* paired with a rule constraining source to the Docker/WSL
virtual subnet. Docker Desktop does not guarantee a stable source subnet across restarts, so the
guidance says so plainly and supplies verification and rollback commands rather than pretending
to a precision the platform does not offer.

**8. Path translation is explicit client-side configuration.** Container paths are not host
paths. The mapping (`/work/commandcenter` → `C:\work\commandcenter`) lives in the *container's*
environment and is applied by the client before calling. HEARTH does no path rewriting and
learns nothing about container mounts — server-side inference would be guessing. Callers are
steered to send text content directly when translation is not actually needed.

## Consequences

- Existing callers are untouched: no `profile` field means today's behavior, and the default
  bind is unchanged. Rollback from container mode is one environment variable plus a gateway
  restart, with no data migration.
- HEARTH gains its first authorization concept. This is a deliberate, small extension of what
  `auth.py:1` calls "frozen contract 3" — two optional fields, absent by default — and not a
  rewrite of the identity model.
- The capability taxonomy becomes a maintenance obligation: a new tool without a mapping fails
  the surface-completeness test. That friction is the point.
- Profiles are reusable for the roles named as the growth path (Builder, Documentation,
  Reviewer, Fleet Orchestrator, Human Operator), so the next integration is a config entry
  rather than another security review.
- `doorcheck`'s exit-code semantics change for the "door up, backend cold" case (1 → 0).
  `/checkmcp` and any consumer keying on the old behavior must be re-read; the fleet watchdog is
  unaffected because `fleet/inventory.toml` gates on a TCP port check and uses doorcheck only as
  the `revive` action.
- Known residual risk, out of scope here: `HearthContext.caller` is assigned per call on a shared
  object (`gateway.py:195`), which is a latent race under concurrent callers. The new enforcement
  deliberately uses contextvars rather than extending that pattern.
- Reversal path: unset `HEARTH_CONTAINER_ACCESS_ENABLED`, restart via the normal
  `start-hearth-gateway.cmd` — the door returns to loopback-only. Profile fields may stay in the
  registry harmlessly; revoking the container caller is a separate one-command operation.

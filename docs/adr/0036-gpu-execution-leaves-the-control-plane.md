# 0036 — GPU execution leaves the control plane: the interactive render agent

**Status:** Accepted (2026-08-25); proven end to end the same day

**Companion to:** `docs/adr#0035` (rendering is its own authority)

## Context

ADR 0035 put `media.render` on the door with its own capability and profile. The
first real job submitted through that door dispatched to a calibrated lane and
then died in ffmpeg:

```
[D3D11VA] Failed to create Direct3D device (887a0004)      # DXGI_ERROR_NOT_FOUND
Failed to set value 'qsv=hw:hw_any,child_device=3,child_device_type=d3d11va'
    for option 'init_hw_device'
```

The cause is not the render pipeline. It is **Windows session isolation**:

| | |
|---|---|
| `HearthGatewayBoot` | `MSFT_TaskBootTrigger`, `LogonType=S4U`, `RunLevel=Highest` |
| gateway process | **session 0** |
| interactive shell | session 1 |

Session 0 has no desktop and no GPU adapter access, so D3D11 device creation
fails outright. The identical ffmpeg command succeeds from session 1, where
two-lane concurrent renders had already been proven on both B70s (29.5% / 27.7%
measured media-engine utilisation). The same root cause explains a separate
long-standing oddity: llama-server's per-process GPU counters read 0 because
`ArcServeBoot` is also S4U. Inference still works there because Vulkan compute
does not need a desktop the way D3D11 device creation does.

## Decision

**Do not change `HearthGatewayBoot`.** Booting at power-on with no logon is why
the door survives a reboot unattended; making it interactive would fix GPU
visibility by weakening availability and coupling an infrastructure service to
whether a user happens to be logged in.

Instead, split ownership along the line the OS actually draws:

> **Gateway owns render authority and job lifecycle.**
> **Interactive render agent owns GPU process execution.**

The gateway remains an always-available session-0 control plane. It owns
authority, admission, receipts and orchestration — but not every hardware
execution context. The B70 renderer has a real OS-level execution-context
requirement that inference and control-plane work do not.

The agent is deliberately narrow. It runs in the interactive session; owns no MCP
door and opens no network listener; understands only queued `media.render` work;
uses the existing cross-process `CapacityLeaseStore`; executes ffmpeg against the
calibrated lanes; performs the existing revision-authority check and promotion
contract; and disappears safely, leaving jobs queued rather than falsely failed.

`submit_render` stays exactly where it is. A caller still talks to HEARTH and
gains no access to a new render daemon.

### The agent does NOT write the ledger

This constraint was measured, not assumed. `ExecutionLedger.append` guards with a
**`threading.Lock`** (in-process only), assigns sequence via
`SELECT MAX(sequence)+1`, and writes at `offset = stat().st_size`. Two processes
appending concurrently to one ledger produced:

```
duplicate sequence 17 written twice        # canonical stream corrupted
sqlite OperationalError, then a cascade of PermissionError
80 appends attempted -> 18 events written
```

`CapacityLeaseStore` being cross-process safe does **not** imply the ledger is.
So the agent communicates through the same local file/sidecar pattern already
used at the BF6 boundary, and the gateway performs every state transition. No new
port is required.

### Protocol

```
hearth/var/render/
  queue/<job_id>.json     gateway -> agent   validated job, ready to execute
  claims/<job_id>.json    agent -> gateway   claimed: lane, pid, started_at
  results/<job_id>.json   agent -> gateway   terminal receipt
  agent.heartbeat.json    agent -> gateway   liveness + capability
```

A claim is an atomic `os.replace` out of `queue/`. The rename is the lock: one
winner, no lock file, no coordination protocol.

### Lifecycle

```
submit_render
  -> gateway validates + persists job          (ledger: request.accepted, job.queued)
  -> job remains durably queued
  -> agent claims execution eligibility        (claims/<job_id>.json)
  -> cross-process lane lease acquired         (render:<lane_id>, limit 1)
  -> ffmpeg runs in session 1
  -> validate + revision check + promote
  -> terminal result handed back               (results/<job_id>.json)
  -> gateway ingests                           (ledger: dispatched .. succeeded/failed)
```

## Consequences

Failure semantics become explicit, and each is a test:

- **no agent running** → lanes report healthy, `interactive_executor_available:
  false`, and submitted jobs stay **queued**. A job with no executor is still a
  valid job; refusing it would turn "nobody is logged in" into a failure.
- **agent starts** → queued work resumes with no resubmission.
- **interactive session dies mid-render** → the claim carries pid and start time;
  the orphaned ffmpeg is reaped and the job returns to `queue/`, not to `failed`.
- **gateway restarts mid-render** → the GPU process is untouched. A result
  arriving for a job the gateway never saw start synthesises the missing
  transitions rather than jumping `queued -> succeeded`.
- **BF6/OBS withholding** continues to operate at lane level, inside the agent's
  lane selection, unchanged.

Verified end to end: a job submitted through the door queued with no executor,
was claimed and rendered by the agent in session 1, and reached `succeeded` with
the canonical lifecycle written entirely by the gateway.

The agent is intended to be folded into the planned OMEN dispatcher rather than
run as a second persistent interactive process — that dispatcher already has to
exist in the correct session for the BF6 sidecar bridge. The modules stay
separable even when they share one process.

## Alternatives considered

**Change `HearthGatewayBoot` to an interactive logon type.** Smallest change,
rejected: it trades an always-available control plane for GPU visibility, and
couples an infrastructure service to an interactive login.

**Let the agent write the ledger directly.** Rejected on measurement — see above.
The corruption is silent and lands in the canonical append-only stream.

**Run renders over a new local RPC to a render daemon.** Rejected: a second
listener is a second door to authenticate, authorise and keep alive, for a
problem the existing sidecar pattern already solves.

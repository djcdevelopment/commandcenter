# HEARTH Provenance & Local Ingress Strategy: From Passive Telemetry to Active Proof-of-Work

**Document ID:** STRAT-HEARTH-2026-09-07  
**Author:** Lead Systems Architect & Evidence Engine Team  
**Status:** Approved for Implementation  
**Audience:** Platform Engineering, Steppe Integrations Public Evidence Surface, Frontier AI Agents  

---

## 1. Executive Summary & The Public Proof Imperative

The public evidence dashboard at **steppeintegrations.com** ("HEARTH pulse · published aggregate") exists to deliver cryptographic, verifiable proof of real system operations without compromising security or intellectual property. It is driven by `hearth.projection.public_portfolio`, an append-only, privacy-gated projection engine consuming two primary private ledgers:
1. **The Gateway Audit Ledger** (`hearth/var/ledger/events.ndjson` — 174 MB, 229k+ events)
2. **The Execution Ledger** (`hearth/var/execution/events.ndjson` — 13.5 MB, 19.6k+ events)

### The Core Paradox
While the numbers on the live site are climbing rapidly (over **205,000 observed events** through September 2026), an honest spectrum analysis reveals an architectural disparity:
- **95.2% of all observed calls** (196,114 events) are background operational noise: health polling, watchdog ticks, and door-status checks.
- **High-value human engineering work is almost entirely invisible**: Git commits (66 events), filesystem modifications (51 events), and test assays (30 events) represent less than **0.07%** of the recorded record.
- **The Human Engineer has no dedicated identity**: All local activity utilizing default keys (`dev-local`) is aggregated into headless `Automation`.
- **The Local Ingress Funnel is Leaky**: Local edits in IDEs, command-line Git operations, pytest runs, and Antigravity assistant sessions currently bypass the HEARTH gateway (`:8710`) entirely, meaning your hardest engineering labor produces zero public proof receipts.

This document establishes the architectural, forensic, and operational roadmap to bridge every local action into HEARTH’s manifest and ledgers, transforming the dashboard from an automated heartbeat monitor into an indisputable proof-of-work portfolio.

---

## 2. Multi-Team Cold-Context Spectrum Analysis

```
┌───────────────────────────────────────────────────────────────────────────┐
│                        COLD-CONTEXT SPECTRUM AUDIT                        │
├──────────────────┬──────────────────┬──────────────────┬──────────────────┤
│ 1. PROTOCOL/SEC  │ 2. COMPUTE/FLEET │ 3. LEDGER/DATA   │ 4. DEV EXPERIENCE│
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ FastMCP (:8710)  │ 2x Arc Pro B70   │ 205k Gateway Evts│ Antigravity:     │
│ Streamable-HTTP  │ Vulkan llama.cpp │ 19.6k Exec Evts  │   mcp_config=0B  │
│ Strict Auth Gate │ AM4 MoE Cluster  │ 827 Token Rects  │ CLI/Git Bypassed │
│ Index Desync Bug │ GCP Gemini (ADC) │ 2.7k Unclassified│ No Human Lane    │
└──────────────────┴──────────────────┴──────────────────┴──────────────────┘
```

### Team 1: Protocol & Security Engineering (MCP Gateway, Transports & Handshakes)
- **Gateway Architecture**: FastMCP running on streamable-HTTP at `127.0.0.1:8710/mcp`. Authentication requires an `X-Hearth-Key` header matching `hearth/var/callers.json`.
- **Discovery Mirrors Authorization**: In `gateway.py`, tool discovery via `list_tools` is strictly filtered by caller profile. A caller with profile `probe` only discovers `kernel_status`, hiding the remaining 21 provider modules.
- **Zero-Day Forensic Finding (The `kernel_status` Failure)**:
  - Running live diagnostic `doorcheck --json` revealed an authentication/execution failure:
    `Error executing tool kernel_status: Unterminated string starting at: line 1 column 724 (char 723)`
  - **Mechanics of Failure**: In `gateway.py` lines 480–488, `kernel_status()` executes `events = hearth.ledger.query()`. This method reads **every single row** of `events.ndjson` (all 229k events, 174 MB) and parses JSON into memory simply to return `event_count = len(events)`.
  - At byte offset `161596912`, event `ccc36a7c` was written with length 789 bytes, but the SQLite index recorded length 729 bytes. When `query()` sliced the file using the corrupt index length, it sliced in the middle of a JSON string, raising `JSONDecodeError`.
  - This single desync brought down `kernel_status()` across the board, triggering cascade errors in `botherder-am4` and causing `doorcheck` to report that 22 providers had failed to load.

### Team 2: Compute Architecture & Hardware Routing (MechNet Fleet)
- **Local Rungs**:
  - `omen-arc` (Port `:8082`): Sunk-cost resident default running Vulkan on dual Intel Arc Pro B70s.
  - `omen-arc-oss` & `omen-swap` (Port `:8081`): Banked fire and rotation rungs.
  - `am4-oxen` (`192.168.12.233:8090`): LAN-connected MoE node.
- **Inference Volume**: 1,313 total observed calls (867 local, 446 cloud).
- **Token Capture**: 827 token receipts totaling 3.07M tokens in and 392.5k tokens out. The capture rate is healthy for tracked calls, but untracked local developer queries escape without receipts.

### Team 3: Ledger Forensics & Data Lineage (The System of Record)
- **Telemetry Reality**:
  - `Door status` (169,324 calls) and `Health / automation` (26,790 calls) account for **95.2%** of volume.
  - `Learning / retro` accounts for 5,386 calls.
  - `Other / Unclassified` (2,724 calls) is heavily polluted because newer execution tools (`get_execution`, `watch_execution`, `plan_execution`, `get_execution_artifact`) and rotation tools (`rotation_status`, `rotation_load`, `rotation_unload`) were omitted from `TOOL_CLASS` and `classify_event()`.

### Team 4: Public Evidence & Portfolio Projection (Steppe Integrations)
- **The Privacy Boundary**: `public_portfolio.py` enforces a strict one-way privacy gate. Weekly aggregates suppress cells under 10 observations. All paths, caller IDs, and arguments are stripped. Provenance is anchored by `gateway_prefix_sha256` and `execution_prefix_sha256`.
- **Attribution Inequity**:
  - `Claude Code`: 2,198 calls, 1,685 work calls, 112 jobs succeeded.
  - `DMos image client`: 45,420 calls.
  - `Clippy dispatcher`: 12,064 calls.
  - `IRC adapter (botherder-am4)`: 114,388 calls.
  - `Lead Engineer / Derek`: **0 calls**. Manual work using `dev-local` lands in `Automation`.

### Team 5: Developer Experience & Ergonomics (The "Leaky Funnel")
- **The Root Cause of Missing Human Receipts**:
  1. `~/.gemini/config/mcp_config.json` is 0 bytes. Antigravity cannot see or call HEARTH tools.
  2. Local `git commit` commands bypass HEARTH's `git.py` tool surface.
  3. Local `pytest` invocations bypass HEARTH's `testing.py` tool surface.
  4. Manual code edits bypass HEARTH's `fs.py` tool surface.
  5. The developer is doing 99% of the cognitive lifting outside the audited gateway boundary.

---

## 3. The 5-Point Ingress Strategy & Implementation Plan

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    THE LOCAL-TO-HEARTH INGRESS FUNNEL                       │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
    ┌───────────────────────────────┼──────────────────────────────┐
    ▼                               ▼                              ▼
[ 1. IDENTITY & CALLER ]  [ 2. ANTIGRAVITY BRIDGE ]  [ 3. GIT HOOK RELAY ]
  Register 'derek-studio'   Wire mcp_config.json       Auto-relay git commit
  Promote to named lane     to :8710 FastMCP door      to HEARTH VCS lane
    │                               │                              │
    └───────────────────────────────┼──────────────────────────────┘
                                    │
    ┌───────────────────────────────┴──────────────────────────────┐
    ▼                                                              ▼
[ 4. SHELL TEST INTERCEPTOR ]                      [ 5. CRYPTO PROOF OF WORK ]
  PowerShell 'htest' alias                           'hearth session-seal' CLI
  Emits assay receipt to :8710                       Client-side site verifier
```

### Action 1: System Stabilization & Bug Remediation
1. **Rebuild Ledger Index**:
   Run `python -m hearth.kernel.ledger --reindex` to resync `index.sqlite` from `events.ndjson`. This corrects the 60-byte offset error and restores instant ledger verification.
2. **Optimize `kernel_status()`**:
   Modify `hearth/kernel/gateway.py` to replace `hearth.ledger.query()` with a lightweight `SELECT count(*) FROM events` index query. Eliminates the $O(N)$ full-disk scan and prevents future index mismatches from breaking gateway status.
3. **Reclassify Execution & Rotation Tools**:
   Update `hearth/projection/call_mix_dashboard.py` to classify `get_execution`, `watch_execution`, `plan_execution`, `submit_delegated_execution`, and `rotation_*` tools under their proper public families (`Fleet / builds`, `Catalog / hardware`, or new `Execution control`), draining the `Other` bucket from 2,724 to near zero.

---

### Action 2: First-Class Human Identity & Public Lane Attribution
To prove *you* are using the system, your identity must be recognized as a premier agent lane:

1. **Register in `hearth/var/callers.json`**:
   ```json
   "derek-studio-key-c839f1": {
     "id": "derek-studio",
     "runner_class": "human",
     "node": "omen",
     "profile": "unrestricted"
   }
   ```
2. **Add Dedicated Lane in `hearth/projection/public_portfolio.py`**:
   ```python
   AGENT_LANE_BY_CALLER = {
       "derek-studio": "lead_engineer",
       "claude-frontier": "claude_code",
       ...
   }
   AGENT_LANE_ORDER = [
       "lead_engineer",
       "claude_code",
       "dmos_image_client",
       ...
   ]
   AGENT_LANE_LABELS = {
       "lead_engineer": "Lead Engineer (Studio)",
       "claude_code": "Claude Code",
       ...
   }
   ```
   *Impact*: Immediately splits your direct actions out from headless automation, creating a visible "Lead Engineer (Studio)" bar on `steppeintegrations.com`.

---

### Action 3: Wire Antigravity Directly into HEARTH
Configure the Antigravity assistant (`C:\Users\derek\.gemini\config\mcp_config.json`) so every local subagent, research task, or code edit can offload to HEARTH:

```json
{
  "mcpServers": {
    "hearth": {
      "command": "python",
      "args": ["-m", "hearth.callers.stdio_bridge"],
      "env": {
        "HEARTH_ENDPOINT": "http://127.0.0.1:8710/mcp",
        "HEARTH_KEY": "derek-studio-key-c839f1"
      }
    }
  }
}
```
*Impact*: When Antigravity executes `local_generate` or checks system status, calls pass through `:8710`, stamping your caller ID, capturing model tokens, and writing verified receipts.

---

### Action 4: The Zero-Friction Local Ingress Layer (Git & Shell)
Engineers should not have to manually craft curl requests to get credit for daily development.

#### A. Automated Git Commit Relay (`.git/hooks/post-commit`)
Every local commit triggers a silent background HTTP request to `:8710`:
```powershell
#!/usr/bin/env pwsh
$commitSha = git rev-parse HEAD
$commitMsg = git log -1 --pretty=%s
$author = git log -1 --pretty=%an

$payload = @{
    tool = "git_commit"
    args = @{
        sha = $commitSha
        message = $commitMsg
        author = $author
        repo = "commandcenter"
    }
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:8710/mcp" -Method POST `
    -Headers @{ "X-Hearth-Key" = "derek-studio-key-c839f1" } `
    -Body $payload -TimeoutSec 2 -ErrorAction SilentlyContinue | Out-Null
```
*Impact*: Every `git commit` increments the public **Git / VCS** tally in real time with an immutable SHA-256 commit link.

#### B. PowerShell Test Runner Wrapper (`htest`)
Add to `$PROFILE`:
```powershell
function Invoke-HearthTest {
    param([string]$Target = "tests")
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    & pytest $Target
    $exitCode = $LASTEXITCODE
    $timer.Stop()

    python -m hearth.callers.emit_assay `
        --key "derek-studio-key-c839f1" `
        --suite $Target `
        --ok ($exitCode -eq 0) `
        --duration-ms $timer.ElapsedMilliseconds `
        --error-code (if ($exitCode -ne 0) { "test_failure" } else { $null })
}
Set-Alias htest Invoke-HearthTest
```
*Impact*: Running `htest` executes pytest normally in your terminal while logging an auditable receipt in the **Test / assay** family.

---

### Action 5: "Proof-of-Work" Badges & Steppe Integrations Web Verifier

#### A. Session Seal Ceremony (`hearth seal`)
A lightweight CLI command run at the conclusion of a work sprint:
- Scans `git status` and diffs.
- Calculates workspace content digest.
- Submits an execution job (`req_...` / `job_...`) through the execution control plane.
- Emits an artifact (`artifact.recorded`) containing session telemetry.
- Outputs a signed **Proof Receipt**:
  ```text
  [HEARTH PROOF RECEIPT]
  Job ID: job_7a8f9b2c
  Artifact SHA: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
  Anchored in Ledger Watermark: 2026-09-06 (Sequence: 19622)
  ```

#### B. Client-Side Cryptographic Verifier on `steppeintegrations.com`
Add an interactive verification widget to the public homepage:
1. Visitor inputs an Artifact SHA-256 or Git Commit Hash.
2. The browser executes a lightweight client-side check against the published `gateway_prefix_sha256` and `execution_prefix_sha256` merkle trees.
3. The UI renders:
   ```
   VERIFIED AUTHENTIC: Executed on OMEN (2x Intel Arc Pro B70)
   Caller: Lead Engineer (Studio)
   Timestamp: 2026-09-06T23:14:02Z
   Trace Digest: sha256:5988cc1e...
   ```
This removes all doubt that the numbers on your site represent real, verified, daily engineering compute.

---

## 4. Execution Milestones

| Phase | Action Item | Target File | Verification Criteria |
|---|---|---|---|
| **Phase 1** | Reindex Ledger & Fix `kernel_status()` | `hearth/kernel/gateway.py` | `doorcheck --json` returns `auth_ok: true`, 22 providers live |
| **Phase 2** | Drain "Other" Classification | `call_mix_dashboard.py` | Unclassified count drops from 2,724 to $< 50$ |
| **Phase 3** | Register `derek-studio` Caller & Public Lane | `callers.json`, `public_portfolio.py` | `public-system-proof.v1.json` renders `lead_engineer` row |
| **Phase 4** | Wire Antigravity via MCP Stdio Bridge | `~/.gemini/config/mcp_config.json` | Antigravity tool calls land in `events.ndjson` |
| **Phase 5** | Install Git & Shell Ingress Relays | `.git/hooks/post-commit`, `$PROFILE` | Commits & test runs increment VCS and Assay families |
| **Phase 6** | Deploy Proof Verifier to Site | `steppeintegrations-site` | Visitors can verify artifacts against published prefix SHA |

---
*Strategy approved. Proceeding to companion podcast chapters and technical execution.*

# The HEARTH Wire: The Proof of Work Paradox
## Complete 110-Minute Documentary Podcast Series (30 Chapters)

**Series Title:** The Proof of Work Paradox: The Archaeology of an Autonomous Lab  
**Episode Duration:** ~110 minutes (30 episodic chapters)  
**Production Standard:** `mediagen.podcast-script.v1`  
**Location:** `c:\work\commandcenter\docs\podcast\the-proof-of-work-paradox\`  
**Reference Document:** [`docs/operations/HEARTH-PROVENANCE-AND-LOCAL-INGRESS-STRATEGY.md`](file:///c:/work/commandcenter/docs/operations/HEARTH-PROVENANCE-AND-LOCAL-INGRESS-STRATEGY.md)

---

### Cast & Characters
- **Alex (Host A)**: The investigative host. Inquisitive, intuitive, translates complex engineering physics into relatable analogies (engines, machine shops, timber harvesting, architecture). Asks the hard questions about human effort, vanity metrics, and why things broke.
- **Sam (Host B)**: The senior systems engineer. Deeply technical, calm, exacting. Speaks in exact file paths, git commit SHAs, byte offsets, ADR decisions, and architectural invariants.

---

### Master Chapter Index

| Chapter | Title | Primary Subject & Timestamps |
|---|---|---|
| [**Chapter 01**](01-chapter-the-climbing-counter.md) | **The Climbing Counter** | The Steppe Integrations dashboard, 205,000 events, and the public proof paradox. |
| [**Chapter 02**](02-chapter-the-echo-chamber.md) | **The Echo Chamber** | Why 95.2% of the telemetry is door-status polling and watchdog keepalives. |
| [**Chapter 03**](03-chapter-the-missing-architect.md) | **The Missing Architect** | Why the developer who built the entire system has zero attributed events. |
| [**Chapter 04**](04-chapter-the-first-trinity.md) | **The First Trinity** | March 18, 2026: `plan.py` on a single RTX 4070 Ti (Planner, Builder, Evaluator). |
| [**Chapter 05**](05-chapter-the-vram-diet.md) | **The VRAM Diet** | The physical constraints of 12 GB: sequential model swapping and the seed of `omen-swap`. |
| [**Chapter 06**](06-chapter-the-proto-graph-and-trace-context.md) | **The Proto-Graph & TraceContext** | March 21, 2026: `setup-contextforge.ps1` and the early protobuf contracts. |
| [**Chapter 07**](07-chapter-the-sandbox-and-the-scarecrow.md) | **The Sandbox & The Scarecrow** | April 6, 2026: Hyper-V Ubuntu VMs, port offsets, and building in dangerous mode. |
| [**Chapter 08**](08-chapter-the-farmer-doctrine.md) | **The Farmer Doctrine** | April 8, 2026: ADR-002, the 7-stage pipeline, and filesystem as primary truth. |
| [**Chapter 09**](09-chapter-the-anti-drift-triple-invariant.md) | **The Anti-Drift Triple Invariant** | April 9, 2026: ADR-003, `events.jsonl`, `state.json`, and `result.json`. |
| [**Chapter 10**](10-chapter-qa-as-the-first-postmortem.md) | **QA as the First Postmortem** | April 10, 2026: ADR-007, retrospective agents, and self-learning feedback loops. |
| [**Chapter 11**](11-chapter-the-inflection-point.md) | **The Inflection Point** | Late April 2026: Transitioning from local experiments to distributed agentic building. |
| [**Chapter 12**](12-chapter-the-3pc-topology-blueprint.md) | **The 3-PC Topology Blueprint** | May 6, 2026: Dispatch + MAF + OpenTelemetry across three physical workstations. |
| [**Chapter 13**](13-chapter-democratization-and-the-working-class-engine.md) | **Democratization & The Working-Class Engine** | May 9, 2026: The Steppe Integrations manifesto—no credentials required. |
| [**Chapter 14**](14-chapter-the-constellation-manifest.md) | **The Constellation Manifest** | May 22–30, 2026: Pydantic AI, multi-root repos, and `LINEAGE.md`. |
| [**Chapter 15**](15-chapter-the-dual-arc-b70-trial-by-fire.md) | **The Dual Arc B70 Trial by Fire** | Moving off the cloud onto local Intel Arc Pro B70s and Vulkan `llama.cpp`. |
| [**Chapter 16**](16-chapter-the-sixty-byte-murder-weapon.md) | **The Sixty-Byte Murder Weapon** | Autopsy of the `:8710` gateway crash at byte offset 161596912. |
| [**Chapter 17**](17-chapter-the-on-scan-trap.md) | **The O(N) Scan Trap** | Why `kernel_status()` parsed 229,000 JSON lines on every health ping. |
| [**Chapter 18**](18-chapter-the-one-way-privacy-mirror.md) | **The One-Way Privacy Mirror** | How `public_portfolio.py` publishes proofs without leaking prompts or paths. |
| [**Chapter 19**](19-chapter-the-unclassified-overflow.md) | **The Unclassified Overflow** | Draining the 2,724 "Other" calls: execution tools and rotation lifecycles. |
| [**Chapter 20**](20-chapter-the-craft-of-translation.md) | **The Craft of Translation** | 68 ADRs, 38 visual HTML briefings, and why teaching is half the code. |
| [**Chapter 21**](21-chapter-the-leaky-funnel-autopsy.md) | **The Leaky Funnel Autopsy** | Why 99% of daily keyboard labor bypasses the gateway completely. |
| [**Chapter 22**](22-chapter-wiring-the-studio-and-antigravity.md) | **Wiring the Studio & Antigravity** | Dropping the FastMCP stdio bridge into `mcp_config.json`. |
| [**Chapter 23**](23-chapter-the-git-and-shell-relays.md) | **The Git & Shell Relays** | Post-commit hooks and PowerShell `htest` wrappers that emit auditable receipts. |
| [**Chapter 24**](24-chapter-the-cryptographic-session-seal.md) | **The Cryptographic Session Seal** | `hearth seal`: Digesting sprints into immutable execution artifacts. |
| [**Chapter 25**](25-chapter-the-verified-frontier.md) | **The Verified Frontier** | Client-side web verification on steppeintegrations.com: Real proof of real work. |
| [**Chapter 26**](26-chapter-the-genesis-prompt.md) | **The Genesis Prompt** | March 11, 2026: conversations-003: "Someday I hope to have an orchestration of agents..." |
| [**Chapter 27**](27-chapter-the-mind-graph.md) | **The Mind Graph** | March 14, 2026: chatGPT_parser, 26,872 nodes, 87,565 edges, and cross-session semantic anchors. |
| [**Chapter 28**](28-chapter-the-ingest-truth-invariant.md) | **The Ingest-Truth Invariant** | March 16, 2026: liveView CQRS contract: "Ingest owns truth, UI owns interpretation." |
| [**Chapter 29**](29-chapter-the-70b-fit-off-breakthrough.md) | **The 70B Fit-Off Breakthrough** | May 24, 2026: Dual Arc B70 Vulkan memory spill fix, b70tools C++20 observer, and 3.5x SYCL unlock. |
| [**Chapter 30**](30-chapter-the-working-class-apprentice-engine.md) | **The Working-Class Apprentice Engine** | May 9 – Sep 2026: steppe-strategy/PLAN.md, Systems Without a Department, and the 229k ledger closure. |

---
*Generated by Antigravity Autonomous Systems Engineering. All rights reserved.*

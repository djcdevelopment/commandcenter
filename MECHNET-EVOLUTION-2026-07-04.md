# The Mechnet: How a Pile of Consumer PCs Became a Self-Observing AI Lab

*Written 2026-07-04, for a fellow AI hobbyist. This is the story of how one Windows box running copy-paste prompts evolved into a distributed, self-healing, MCP-fronted agent fleet — and what we learned at each step.*

---

## What you're looking at

**Mechnet** is our name for the whole ecosystem: a handful of consumer machines (nothing exotic — Ryzen desktops, an OMEN gaming rig, Hyper-V VMs) wired together into a network of AI builders, planners, watchers, and one always-on gateway. The name comes from the "mech suit" metaphor: the human doesn't type the code, he pilots a suit that does — and eventually the suit became a *team* of suits.

The headline numbers, roughly six months in:

- **~50 repos, millions of lines** of agentic-CLI development, directed rather than typed
- **4+ builder VMs** plus a local-model worker, all dispatchable from one inbox
- **One MCP gateway** (codename **HEARTH**) through which *every* offload crosses — frontier or local — so every unit of work lands on a ledger and feeds a learning loop
- **677+ passing tests** on the gateway kernel alone, a 15-minute autonomous patrol that heals its own infrastructure, and a CP-SAT job-shop scheduler that plans GPU work like a factory floor

None of it needed cloud GPUs. That's kind of the point.

---

## The hardware (deliberately unglamorous)

| Node | Role |
|---|---|
| **OMEN** (RTX 5070) | The conductor's home. Runs the HEARTH gateway, Ollama (`qwen3-coder:30b` @ ~54 tok/s), the watchdog, and Hyper-V hosting the builder VMs |
| **AM4** (Ryzen 9, RTX 4070 Ti + 2× Intel Arc Pro, ~64GB VRAM total) | The heavy local-inference box: dual ComfyUI image-gen on Arc, a mature .NET model-lifecycle layer (`vllama`) with OOM safety gates and serve-truth readiness checks |
| **cc-conductor** (VM) | Canonical repo + task inbox + the "living plan" web server |
| **cc-builder-1..4, claudefarm1** (VMs) | Competing build agents — some frontier-backed (Claude), some local-model-backed (Qwen), racing on the same briefs |

The founding thesis: local inference on Windows + consumer GPUs is real and overlooked. Everything below is downstream of proving that.

---

## The evolution, in six phases

### Phase 1–2: Copy-paste → the Mech Suit

Like everyone, it started with pasting code into a chat window. The first real methodology (published as the **Mech Suit Methodology**) formalized six phases from copy-paste up to multi-agent orchestration: the human describes *effects*, the agent finds the mechanism and instruments it. The repo sprawl from this era isn't cruft — it reads like an athlete's training log: find the limit, build the next version.

### Phase 3: One box hits its ceiling

The whole system was actually *designed* on paper months before it was built — builder farm, QA scoring, association engine, OTel observability. The design was right; the single Windows box wasn't enough to run it. That's the moment most hobbyists stop. We treated it as a provisioning problem instead.

### Phase 4: The fleet (SSH era)

Hyper-V VMs off a golden base image, each running a coding agent, coordinated over SSH by a **conductor**:

- **Speak → board → build**: an idea pipeline turns a described intent into a plan (competing planner models + an ensemble critic), which chains into a build inbox
- **Competing build-outs**: the same brief goes to multiple builders — frontier Claude vs. local Qwen — and a **dynamic assay** grades the results by *passing behavior*, not filenames. The local 30B model won its debut match and got promoted. That was a big day.
- **Checkpoint/resume, auto-start, health sweeps** — boring VM hygiene that turned out to be half the actual work

Key lesson from this era: our assay was a *regression gate*, not an *acceptance oracle* — a null-action submission could win. Finding that (and writing it down as ADR-0001) mattered more than any feature.

### Phase 5: The MCP inversion — HEARTH

This is the part most relevant to "MCP evolution." The SSH era had a topology problem: every session hand-rolled its own connections, and observability was bolted on per-client. The fix was an **inversion**:

> Make the lab a **single writer** behind **one always-on MCP gateway**.

HEARTH is that gateway — an MCP door on OMEN (port 8710) that fronts *everything*:

- `local_generate` — synchronous offload to the boot-started local model (summaries, extraction, boilerplate, triage). First call after boot pays a ~12s model-load tax; after that it's warm.
- `submit_task` / `task_status` — an async task lane that dispatches minutes-scale briefs to the fleet via the conductor's inbox
- File, git, test, and knowledge-query tools — all scoped, all ledgered

The doctrine got a name: **one boundary, three planes** (ADR-0005). Sensing (AM4's MCP), acting (mechnet), and *one door* between an agent and the lab — so every offload crossing produces a ledger event, and the ledger becomes a complete dataset for free. Capture is the moat; the learning on top is commodity.

The MCP client story evolved too: early on we hand-rolled Python clients against the gateway. Now the server is just declared in the repo's `.mcp.json` and every Claude Code session gets `mcp__hearth__*` tools natively. The lesson: what felt like a missing capability was actually just an approval gap in config. Check the boring explanation first.

### Phase 6: The lab learns and guards itself

With everything flowing through one door, the compounding features became cheap:

- **Two economies** (metered frontier tokens vs. sunk local compute) became an explicit doctrine — the metric is *knowledge per local hour*. **Banked Fire** arms idle machines to drain queued work overnight on electricity instead of tokens.
- **CQRS/event-sourcing standardization** — the ledger got formalized: atomic writes, reindex/verify, a corpus digest, `rebuild --from-zero` with a golden determinism test. Two ledgers turned out to be two *bounded contexts*, not a bug (ADR-0010).
- **Watchfire** (ADR-0007) — the watchdog was upgraded from liveness-pinging to **coherence watching**. The guard dog has three faces: *patrol* (observe, snapshot every 5 min, trend over 15), *remediate* (the only mutator, allowed to auto-heal only gaps that are obvious **and** reversible — e.g. phantom in-flight tasks), and *dream* (an off-duty art spell, because a lab should have some soul). Policy: act on the obvious and undoable, flag the ambiguous.
- **The job-shop scheduler** — the newest layer, and a full-circle moment: a 2019 article about rebuilding a manufacturing MES with OR-Tools turned out to be the destination all along. GPU model residency is machine setup time; model swaps are changeovers. A CP-SAT scheduler with a two-economies objective now plans local inference loads — on a 250-job image-gen assay it hit OPTIMAL with 3 model loads and 0 cache misses vs. FIFO's 24 loads and 19 misses. Actuation is deliberately gated behind an advisory period (ADR-0008): the scheduler must first prove, on a ledgered regret trend, that its plans beat what actually happened.

---

## What we'd tell another hobbyist

1. **Put one door between your agents and your infrastructure.** The single MCP gateway is the highest-leverage decision in the whole system. One boundary → complete capture → a learning loop you didn't have to design.
2. **Make agents compete, then grade behavior.** Two builders on the same brief, graded by passing tests, teaches you more about local-vs-frontier than any benchmark. And audit your grader — ours had a null-action exploit.
3. **Respect the two economies.** Frontier tokens are metered; your GPUs are sunk cost. Route summarization, extraction, and boilerplate to the local model; keep frontier reasoning for architecture and judgment. Then *ledger the routing decision itself* — it's data.
4. **Coherence-watch, don't just liveness-ping.** "The process is up" and "the system's state is sane" are different questions. Auto-heal only what is obvious *and* reversible.
5. **Decisions are artifacts.** Everything above is pinned by ADRs (0001–0011 in `docs/adr/`), dated HTML review documents, and session retros. Six months from now, "why is the double-write intentional?" has a one-file answer (ADR-0011).
6. **Never say impossible.** Standing house rule: enumerate the untested approaches and exhaust them first. The fleet exists because "one box can't do this" was treated as untested rather than true.

---

## Where it stands today

The instrument is built and live: door up, ledger flowing, patrol walking its rounds every 15 minutes, scheduler advising, builders racing. The current frontier is *closing loops* — acceptance assays that can't be gamed, scheduler actuation earned via regret trends, and retros that audit whether last session's lessons were actually acted on.

The suit became a team. The team is learning to run the shop.

---

*Naming note, since every hobbyist project needs a mythos: the repo family follows a "fire on the steppe" cosmology — HEARTH (the always-on gateway), ember (idea-to-PR seed), Banked Fire (idle-drain), Watchfire (the guard). The fire is the always-on local compute; the steppe is the network it lights.*

# Lineage — the receipted pre-history of commandcenter

Assembled 2026-07-10 from five read-only agent surveys (Sonnet, over SSH to the
AM4 `/mnt/win/work` mount and local clones). This directory is the durable
landing of that archaeology: the synthesis here, the raw survey reports as
sibling files. Nothing in here is design guidance — it is the *evidence trail*
of where the design came from, kept so the story survives a restart.

## Why this exists

Three reasons:

1. **Proof of foresight is the product.** Dated artifacts showing the system
   was designed, built, audited, and rebuilt across 2026 — before the market
   caught up — are a positioning asset. Receipts beat retellings.
2. **Convergence source.** commandcenter's house rule is convergence, not
   greenfield. When a "new" capability is proposed, check here first: an
   ancestor probably exists (it did for the scheduler, the ledger, the retro
   discipline, and the on-ramp/manifest idea).
3. **Corrections to the oral history.** Working memory had drifted ("MAF+OTel
   from day one"). The file dates say otherwise, and the true story is
   stronger.

## The acts, dated

| Act | When | What | Where | Evidence |
|---|---|---|---|---|
| **Pre-history** | 2017–2019 | Nike Air Manufacturing Innovation: global production-planning platform, $300mm+ annual orders, 24/7 shop. The job shop lived, not studied. | LinkedIn (public) | Role listing; 2019 article |
| **The abstraction** | 2019-11-11 | "Solving the Job Shop Problem" — OR-Tools/CP-SAT, CPS↔MES data modeling. Written after living it. | LinkedIn Pulse; republished on steppeintegrations.com with 2026 coda | Article date |
| **Act 1 — doctrine** | 2026-03-06→14 | Artifact doctrine born, framework-free: `ai-dev-system` ("Every meaningful action produces a durable artifact"), `ai-systems-research` (session ledger, 7-artifact bundles, re-entry protocol), `liveView` ("ingest owns truth, ui owns interpretation"). Plain Python/JSON/JSONL/PowerShell. Zero MAF/OTel/MCP. | `/mnt/win/work/` | [survey-doctrine-repos.md](survey-doctrine-repos.md), [survey-liveview-writing.md](survey-liveview-writing.md) |
| **Act 0 — planner/critic** | 2026-03-16→Apr 8 | The origin constellation at `/mnt/win/work/start/`: `planner` gen-1 (Python, 1,660 commits — git *was* the run ledger; all-local Mistral/Qwen/Llama on a 4070 Ti; ASSESS→DECIDE, verdicts accept/refine/restructure/stop) → `precheck` gen-2 (.NET port) → `ashley` gen-3 (rebuild, ~9,500 LOC in two nights). Plus `contextforge` (association-engine seed), `scarecrow` (Hyper-V VM builder orchestration — mechnet's direct ancestor), `portmap`. | `/mnt/win/work/start/` | [survey-origin-constellation.md](survey-origin-constellation.md) |
| **Act 2 — Farmer** | 2026-04-06→16 | The 10-day sprint (81 commits, 133 tests): .NET control plane, 7-stage RunWorkflow, Claude CLI workers full-dangerous on Hyper-V VMs, **MAF retrospective agent** (MAF v1.0 shipped Apr 3; built on it by Apr 6), OTel→Jaeger, NATS, immutable run dirs + anti-drift contract. QA-as-postmortem born (ADR-007: "that is still data, which means that is still success"). The `MAF.png` LucidChart dates to this week — left half = Planner/Ashley lineage, right half = Scarecrow/Farmer. | GitHub `djcdevelopment/planning` (in-repo name: Farmer) | [survey-farmer.md](survey-farmer.md) |
| **Act 3 — self-audit** | 2026-05-22→28 | `bridge-synthesis-2026-05-26/` judges the March substrate against the field: **"Field right, repo wrong"** — sessions should emit OTel, not markdown bundles. `MANIFEST_LINK.md` + `constellation.yaml` point to a next-gen manifest framework (`D:\work\gad\pm\manifest\`) — ancestor of the fleet-manifest/on-ramp idea. | `ai-systems-research` (May layer) | [survey-doctrine-repos.md](survey-doctrine-repos.md) §Repo 1 |
| **Act 4 — commandcenter** | 2026-06-29→ | Convergence: fleet, assay, HEARTH ledger, OTel business-ontology spans, /retro, MCP as the one door. The May audit's prescriptions, adopted. | this repo | ADRs 0001–0015, git history |
| **Act 5 — comfy** | 2026-07-01→10 | Second 10-day campaign (69 commits), unrelated domain (Valheim netcode fieldlab). Timestamp archaeology proves the method: code → evidence → dashboard → ADRs → retro written minutes apart; phase boundary (`handoffs/`) frozen to the minute of the pivot; analysis as its own dated phase; pre-staging before founding commits. | `C:\work\comfy` | [survey-comfy-timestamps.md](survey-comfy-timestamps.md) |

## Corrections to the record

- **"MAF + OTel from day one" is false for the lineage** (true only of the
  April diagram). March was framework-free by construction; MAF didn't exist
  until 2026-04-03. The frameworks were an April adoption — within 72 hours of
  MAF shipping — and OTel was *prescribed by the May self-audit* before
  commandcenter adopted it. Documented evolution with a dated course
  correction, not born-perfect design.
- **The builder farm and QA scoring were born in Farmer** (April), not in the
  March design repos. Farmer contains no planner/critic ensemble, no
  association engine, no CP-SAT — it is specifically the
  build-orchestration + QA-postmortem slice.
- **The association engine has two seeds, both unconverged**: `contextforge`
  (2026-03-21, passive capture) and `chatGPT_parser` (scored pairs → ranked
  queue → hubs). It remains the one pillar of the original design commandcenter
  has not absorbed.
- **The planner/critic went through three generations in three weeks**
  (Python → .NET port → full rebuild) before any of today's ensemble work.
  `precheck/plan/` holds ~35 unread planning docs (`v2-risk-critique.md`,
  `v2-feedback-adjudication.md`, `guardrail-effectiveness.md`) — mine these
  before designing new critic-calibration machinery.

## The through-line

The personal thesis under all of it is *cruising timber* (Derek's phrase, from
walking the land with his father each fall to plan the winter's cut): invisible
preparatory work made legible, "not for ego — for continuity." The ledger,
ADRs, retros, and this directory are the cruise made self-documenting. Best
single line in the corpus, from the writing workspace:

> "If AI work cannot survive a restart, it is not a system yet."

## Provenance

Surveys executed 2026-07-10 by Sonnet subagents (read-only: SSH `homebase` →
AM4 NTFS mount; local clone of `djcdevelopment/planning`; local walk of
`C:\work\comfy`). Reports preserved verbatim below, light formatting only.
Offsite backups of the source material exist; the GitHub-pushed repos
(`start-planner`, `ashley`, `planning`) carry third-party-attested dates.

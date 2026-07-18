---
name: retro
description: Close out a work session — write a multi-role engineering-team retrospective (dual Claude/Derek POV), capture lessons learned, write/update ADRs, and update the affected docs/plans/README/memory. Offloads the draftable prose to HEARTH/mechnet. Use at the end of a session or when the user says "/retro", "write the retro", "wrap this up".
---

# retro — close the session as an engineering team would

Turn a working session into durable artifacts: a retrospective written from an
engineering team's multiple seats (with our collaboration filling those seats),
from **both** Claude's and Derek's perspective, plus the ADRs, doc/README/plan
updates, and memory writes the session earned. Draftable prose is offloaded to
the fleet (HEARTH `local_generate`, optionally `submit_task`); judgment and every
repo-coherent write stay frontier.

**Why the split:** the conversation, the tool calls, and the *why* behind each
decision live only in this context — I am the only one who can see them. The
file-level truth lives in git. So this skill assembles the story from my context,
gets git to confirm the mechanical half, and offloads only the prose a local
model can safely draft from a fact-sheet. See the sibling `checkmcp` skill if a
HEARTH call fails mid-run.

House style to match (read one before writing): latest `SESSION-RETRO-*.md`,
`docs/adr/0001-*.md` + `docs/adr/README.md`. Retros are **markdown** here (HTML is
for living plans — see the html-living-plan directive; mirror to HTML only if the
user asks).

---

## Arguments

- (no arg) — full retro of the current session.
- `--fleet` — also fire an independent, repo-aware second-opinion retro draft at
  the fleet via `submit_task` (async, lands on the ledger). Default is
  inline-only via `local_generate`.
- `--since <ref>` — git range start (default: auto-detect, see Phase 0).
- `--no-offload` — skip HEARTH; draft everything frontier (use if the door is down
  and you don't want the retry tax).

---

## Phase 0 — Gather the factsheet (frontier — only I can do this)

**First, close the loops from last time (reap before you sow):**
- **Reap pending fleet second opinions.** Read the previous retro's Provenance for
  any `--fleet` plan_ids; `mcp__hearth__task_status(plan_id)` each. Fold completed
  verdicts into this retro as a short **"Second opinion resolved"** note (agree /
  disagree / what it caught that we missed). A dispatched-but-never-read second
  opinion is worse than none — it costs tokens and teaches nothing.
- **Audit last retro's lessons for follow-through.** Read the previous retro's
  Lessons section and grade each: `acted-on | pending | dropped` (one-line table
  in the new retro, with a link when acted-on). Retros are cheap; follow-through
  is the value. A lesson pending 2+ retros gets escalated to the open-decisions
  register (Phase 2e) or explicitly dropped with a reason.

Then assemble a compact FACTSHEET. Pull from three sources and keep it tight; it
is the context every later offload depends on (a local draft is only as good as
this).

1. **The conversation arc** (only I have it): the original ask, how it evolved,
   decisions made, dead-ends, things the user corrected. Include the *why*.
2. **Git ground truth** — run from repo root (`C:\work\commandcenter`):
   ```
   git log --oneline --no-decorate <since>..HEAD
   git diff --stat <since>..HEAD
   git status --short
   ```
   Auto-detect `<since>` when not given: the commit where this session started
   (the first commit after the last `SESSION-RETRO-*` docs commit, or `HEAD~N`
   spanning today's work — check `git log --since=<session-start>`).
   **Advisory / zero-commit sessions are normal** (design reviews, fan-outs,
   decisions): if the range is empty, say so plainly, skip the diff-derived doc
   sweep in 2c, and center "What shipped" on artifacts + decisions instead of the
   commit table. Don't stretch the range backward to manufacture commits.
3. **Tools & files I touched this session** (from my context): which files were
   read/edited/written, which MCP/fleet calls were made, what they returned.

Write the FACTSHEET as short structured notes (ask · what shipped · files touched
· decisions · surprises · open threads). Do NOT prettify it — it's raw input.

## Phase 1 — Offload the draftable prose (HEARTH / mechnet)

Unless `--no-offload`: hand the FACTSHEET to the local model for first-pass prose,
then edit. These are exactly the "draft prose you will then edit" + "condense /
extract" cases CLAUDE.md says to offload. **The local model has no repo access —
put every fact it needs in the prompt.** One retry max on `ok:false`; if still
cold or unusable, draft it yourself.

**Give the model one real exemplar:** include ~40 lines of the previous retro's
role-reads section in the prompt as a style sample (local models draft far better
with an in-context example; the edit tax drops measurably).

Offload these sub-jobs (separate `mcp__hearth__local_generate` calls, or one
batched prompt):
- **Timeline condense** — turn the raw arc into a tight chronological narrative.
- **Per-role first passes** — from the FACTSHEET, draft each engineering seat's
  read (roles listed in Phase 2). Give the model the seat + the facts, ask for a
  candid paragraph.
- **Lessons-learned extraction** — pull candidate durable lessons as bullets.

Treat every result as a *draft*: correct hallucinations against the FACTSHEET
(the local model has invented content here before — that's expected, it's why we
edit). **Grade the draft** with a one-word verdict —
`faithful | minor-fixes | hallucinated` — recorded in both the retro's Provenance
line and the Phase 3 event payload (`offload.edit_verdict`). This is free
longitudinal quality data on local-model prose; it's the learning loop the
offload doctrine promises, at zero extra cost. If HEARTH is down, run `checkmcp` once (`doorcheck --revive`); if still
down, fall back to `--no-offload` behavior and note it in the retro's provenance.

**`--fleet` (optional independent draft):** also
`mcp__hearth__submit_task(prompt=<self-contained retro brief incl. a git range to
inspect>, builders=["am4-worker-1"], plan_id_hint="retro-<date>")`. The worker
has read-only source at `~/commandcenter-src`, so tell it to `git log`/`git diff`
that range itself for the file-level half, and put the conversation/decision half
(which it cannot see) in the brief. Note the returned `plan_id`; poll
`task_status` later. This is a second opinion, not a blocker — don't wait on it.

## Phase 2 — Write the artifacts (frontier — needs repo coherence)

### 2a. The retrospective — `SESSION-RETRO-<YYYY-MM-DD>.md`
(If one already exists for today, append a clearly-titled section rather than
overwriting.) Match the house style. Required sections:

- **One-line** — the through-line in one sentence (bold the verb-y core).
- **What this session was** — framing (design vs build vs curate vs recover).
- **What shipped** — a `Commit | What` table built from the git log, plus a list
  of new durable artifacts.
- **The team retro — our collaboration across the seats.** Frame the session as
  an engineering team where Claude + Derek filled multiple roles, and give each
  seat an honest read (what went well / what to change). Cover at least:
  - **Architect** — were the design calls sound? what would we decide differently?
  - **Implementer** — build quality, rework, where the code fought us.
  - **Reviewer / QA** — what caught defects; what slipped; test posture.
  - **Operator / SRE** — fleet/infra reality (nodes, the door, deploys, incidents).
  - **Product / planning** — did we build the right thing; scope creep; pacing.
  Name who drove each seat (Derek intuits effects & paces; I hold the whole & do
  the math/instrumenting — see how-derek-scales) — but keep it about the work.
- **Two seats, two views** — a short **From Claude's seat** and **From Derek's
  seat** subsection. Claude's = what I saw in the collaboration, where I over- or
  under-reached, what I'd want to know next time. Derek's = written *as Derek would
  see it* from his stated preferences and this session's signals (mark it clearly
  as my reconstruction of his view, to be corrected — never put words in his mouth
  as fact).
- **Last time's lessons** — the follow-through table from Phase 0
  (`acted-on | pending | dropped`, one line each). Skip only if there is no prior
  retro with lessons.
- **Second opinion resolved** — verdict(s) from any reaped `--fleet` plan_ids
  (Phase 0). Omit if none were pending.
- **Lessons learned** — the durable ones, numbered; each flags whether it becomes
  an ADR, a memory, or a doc change. Give each a stable id
  (`L-<YYYY-MM-DD>-<n>`, suffix addendum letter if multiple retros that day) so
  the follow-through audit and the lessons read-model can track it across
  sessions.
- **Provenance** — one line: git range, what was offloaded vs frontier (incl. the
  draft `edit_verdict`), `--fleet` plan_id if used. (Honesty per the
  report-faithfully rule.)
- **Offload scorecard (S6)** — call `mcp__hearth__query_offload` and report one
  line: the offload_ratio, per-class token totals (sunk/trial), and est_usd_saved
  from `knowledge/offload.json`. If the projection is stale (>6 h) run
  `project_offload_knowledge` first; if the door is down, note "scorecard
  unavailable". Every retro closes with the number the doctrine optimizes.

### 2b. ADRs — `docs/adr/000N-<slug>.md`
For each durable *decision* (not just a lesson): write a new ADR or update an
existing one, following the numbering + format already in `docs/adr/`. Update
`docs/adr/README.md`'s index. A lesson that changes how we'll decide → ADR; a
fact about the world → memory; a how-to → doc.

### 2c. Docs / plans / README — bounded to what changed
Update only docs the session's changes actually affect — derive the set from the
`git diff --stat`, do **not** sweep the whole repo. Typically: the relevant living
plan (keep the `*.html` plans current per html-living-plan), `README`/quickstart if
the change is user-facing, `POUR-STATUS`/status docs if they track this work.

### 2d. Memory
Write/update memory files for durable facts this session established (per the
memory rules in the system prompt), and add/refresh the one-line pointer in
`MEMORY.md`. Update existing memory rather than duplicating; delete what's now
wrong.

### 2e. Open-decisions register — `DECISIONS-PENDING.md`
Retros, ADRs, and review docs each accumulate "pending Derek decisions" that
scatter. Keep one register at repo root: append this session's open decisions as
`- [ ] <date> — <decision> (source: <link>)`, and check off / prune any the
session resolved (with a link to where it was decided). This is the single place
Derek looks when batching decisions in a downtime window. Bounded: touch only
lines this session created or resolved — no register-wide rewrites.

## Phase 3 — Ledger & report (HEARTH)

- `mcp__hearth__record_event` a compact observation (the retro is itself a
  captured signal — capture-first principle). The ledger enforces a strict
  workflow-event envelope (`tools/workflow/ontology.py`) — use exactly:
  `event_type: "retrospective.created"` (a canonical, terminal type),
  `actor: {type, id}` (an **object**, not a string), and the required
  `retrospective_id`, plus `event_id`, `run_id`, `workflow_id`, `timestamp`
  (ISO-Z), `status`, and a `payload` object. **Structure the payload for
  projection, not just for reading** — the retro is an event stream, so make it a
  read-model source (CQRS applied to the skill itself):
  ```json
  "payload": {
    "summary": "<one-liner>",
    "git_range": "<a>..<b> | none (advisory)",
    "artifacts": ["path", ...],
    "lessons": [{"id": "L-<date>-<n>", "text": "...", "disposition": "adr|memory|doc|practice"}],
    "lessons_followthrough": [{"id": "L-...", "status": "acted-on|pending|dropped"}],
    "offload": {"model": "...", "tokens_out": 0, "edit_verdict": "faithful|minor-fixes|hallucinated"},
    "fleet_plan_id": "<id or null>"
  }
  ```
  A future `project_retros.py` can then materialize `knowledge/lessons.json` — a
  queryable read model of every lesson ever learned ("have we learned this
  before?") — without re-parsing markdown. Note: every `local_generate`/`submit_task` call already
  self-ledgers via the gateway wrapper, so this event is the retro's *own*
  marker, not the offload's. Skip only if HEARTH is down.
- Report to the user: the through-line one-liner, a short table of artifacts
  written/updated (with clickable relative-path links), any ADRs, and — if
  `--fleet` — the pending `plan_id` to check later. Keep it scannable.

## Guardrails

- **Never overwrite a retro or ADR you didn't read first** — append or revise, per
  the look-before-you-write rule.
- **Bound the doc sweep** to the diff-affected set; a retro is not license to
  rewrite the repo.
- **Offload the grunt, not the judgment** — role reads and lessons get *drafted*
  by the fleet but *decided* by me; ADR wording and any multi-file doc coherence
  stay frontier.
- **Report faithfully** — if offload was skipped, tests failed, or a section is a
  reconstruction (Derek's seat), say so.
- **Don't touch cc-conductor beyond `submit_task`'s inbox drop** (the concurrent-
  sessions caution); `--fleet` writes only `inbox/<plan_id>.md`.

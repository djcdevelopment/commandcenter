# commandcenter — agent instructions

## Local-first offload (HEARTH)

The HEARTH gateway (always-on MCP door) exposes a local model via
`mcp__hearth__local_generate` backed by boot-started Ollama on OMEN
(`qwen3-coder:30b` by default). Before spending your own frontier tokens on a
self-contained sub-task, delegate it to the local model. Every such call also
lands on the HEARTH ledger and feeds the learning loop — so offloading is both a
token saving and an assay observation.

**The door routes itself (A1/A2/A3/A4, live 2026-07-17): just call
`local_generate` — pin only with cause.** The router weighs the packed payload
against each rung's declared context budget, skips rungs that can't fit or are
busy, climbs one rung automatically on failure (`routed_by:"escalation:a->b"`),
and pulls trial rungs out of opportunistic routing when the GCP credit runway
is low. Optional `quality=` tier: `"fast"` (default, sunk-first), `"good"`
(prefer near-free flash while credits last), `"best"` (does NOT dispatch —
returns `ask:true` recommending a deliberate `backend="gcp-gemini-pro"` pin).
Trust `routed_by` on the result; the ledger records every decision.

**Reach for `local_generate` — don't reason inline — when the sub-task is:**
- summarizing / condensing a file, log, or diff you have already read
- extracting structured data (fields, lists, JSON) from unstructured text
- generating boilerplate (config, test scaffold, docstring, commit-message draft)
- classifying / labeling / yes-no triage over a chunk of text
- drafting prose you will then edit (retro notes, PR body first pass)

For **async, minutes-scale** work (research briefs, simple builds), the door also
has a task lane: `submit_task` dispatches to the fleet via the conductor's inbox
(returns a `plan_id`; poll `task_status`). The brief must be self-contained, but
fleet workers do have read-only source at `~/commandcenter-src`, so a git range
they can inspect themselves is fair game.

**Backend rungs** (hearth/etc/backends.toml; pass `backend="name"` to pin):
`omen-ollama` qwen (sunk — the default), `am4-oxen` (banked fire, big context),
`gcp-gemini` (Vertex `gemini-3.5-flash` on **GCP trial credits** — near-free
frontier-class while they last: prefer it over spending metered Sonnet/frontier
tokens for self-contained reasoning, drafting, and integration proofs),
`gcp-gemini-pro` (Vertex `gemini-3.1-pro-preview`, same trial credits — the
premium reach: 1M-token context + frontier agentic/coding for the hard,
large-context sub-tasks flash can't carry. It is a **thinking** model that burns
tokens on hidden reasoning before any visible text, so the rung sets a generous
default output budget (`settings.max_tokens = 16384`, a cap not a charge) that
the router applies when you omit `max_tokens` — no more empty `text`. Pin it by
name; it is deliberately untagged so opportunistic routing stays on the cheaper
flash rung).

For **auditable infra builds** (checkable acceptance criteria, receipt wanted),
use the door's **build-request lane**: `create/get/list/update/execute/
close_build_request`; receipts + ledger at
`C:\work\comfy\fieldlab\runs\build-requests`. `close(status="done")` is rejected
unless every criterion has a `passed` row with evidence — write criteria you can
prove. See hearth/BUILD-REQUESTS.md.

**Rules:**
- The model cannot run tools or see this conversation — but don't paste file
  contents: pass `files=["repo/relative/path", ...]` and the door packs the
  scope-guarded contents into the prompt door-side (256 KiB/file, 1 MiB total;
  the `files_packed` manifest rides the result). The sandbox is multi-root
  (`HEARTH_SCOPE`, first root primary): repo-relative paths resolve against
  commandcenter, and **absolute paths under `C:\work` pack files from any other
  repo** (labeled by absolute path in the manifest). Pair with `gcp-gemini-pro`
  for subsystem-scale reads. Only context from outside `C:\work` still travels
  in the prompt body.
- The door already retries once (A2 auto-escalation) — if the result still
  comes back `ok:false` or unusable, do the task yourself; never loop on a
  cold worker.
- If the door itself is down, run the `/checkmcp` skill (doorcheck `--revive`) once.
- First call after a boot pays a ~12s model-load tax; calls after that run at
  ~54 tok/s. Don't treat the cold-start latency as a failure.
- Keep frontier reasoning for what needs it: architecture, multi-file logic,
  judgment, and anything requiring repo-wide context. Offload the grunt work,
  not the thinking.

## Reading the decision record (added 2026-07-30)

The decision record here is **two-tier and event-sourced**, so reading it in the obvious
order gives the wrong answer. Sources before views, always:

1. `C:\work\baseline\Lumberjacks\docs\roadmap\commit-notes.jsonl` — the decision log.
   295 append-only entries, one JSON object per line. Filter `"kind":"decision"`.
2. `...\docs\roadmap\valheim-volunteer-roadmap.json` — current milestone truth
   (`active_milestone`, `platform_readiness`).
3. `docs\adr\**` across **all twelve** ADR directories (see below).
4. `DECISIONS-PENDING.md` in `baseline\` and `baseline\fieldlab\` — the live open-question
   registers.
5. Only then the `.html` views, and only for presentation.

**Never edit a file whose header or footer says "Generated deterministically from … do not
hand-edit this file."** Run its renderer instead: `npm run roadmap:render` /
`workbench:render` (Node, in `Lumberjacks\scripts\*.mjs`), or `hearth\projection\*.py` for
the commandcenter dashboards. `roadmap.html` is a 573 KB **view**; its store is the 315 KB
`commit-notes.jsonl`.

**ADR citations are ambiguous — cite `<register>#<number>`, never a bare number.** There are
**130 decision records across 11 registers** in four naming conventions (`NNNN-`, `NNN-`,
`pd-N-`, `ADR-NNN-`), and **20 numbers collide** — `0001` alone names **eleven** different
decisions. "See ADR 0013" is not a resolvable reference.

Resolve any citation against the generated index:
- `C:\work\handoffs\decision-architecture\adr-index.json` — machine-readable
- `C:\work\handoffs\decision-architecture\adr-index.md` — human-readable

Regenerate with `python -m tools.adr_index` (from `C:\work\commandcenter`); verify with
`python -m tools.adr_index --check`, which exits 1 when the tree has drifted. It is
byte-stable for an unchanged tree — it embeds no wall clock — and it renames nothing. It
also flags **24 groups of byte-identical records** (stale mirrors, e.g.
`comfy/fieldlab/docs/adr` duplicating `baseline/fieldlab/docs/adr`) and **2 files with
encoding damage**. Do not delete a duplicate before checking the index for which copy is
canonical.

**Never infer who authored something from filesystem or git metadata.** Agents commit as the
user; `mtime` cannot distinguish a human from an agent. Measured on this machine:
`I(state-tuple ; human-present) = 0.088` of `0.971` bits — 9.1%. Use the `author` field in
`commit-notes.jsonl` (Codex 198 / Claude 97) or the session transcripts.

**Never report "no decisions found" from a file search.** Every observation channel here has
a different blind spot — builds leave no logs, `git worktree add` writes no reflog, the
largest project in the corpus left no Claude transcript, and append-only loops create no
files. State which channels you checked and what each cannot see. Full write-up:
`C:\work\handoffs\decision-architecture\ADR-decision-record-architecture.md` and
`C:\work\handoffs\ARTIFACT-FINGERPRINT-FORENSICS-2026-07-30.md`.

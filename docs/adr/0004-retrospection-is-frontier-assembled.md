# ADR-0004 — Retrospection context is frontier-assembled; the fleet drafts, it does not author

**Status:** Accepted (2026-07-04) — realized as the `/retro` skill (`.claude/skills/retro/SKILL.md`)
**Context sources:** this session's opening question about `submit_task`;
`hearth/toolsurface/task_lane.py`; `CLAUDE.md` local-first offload rules.

## Context

The question that started the `/retro` build was whether HEARTH's `submit_task` could carry
enough context to *author* a retrospective — hand off "the conversation plus the tool calls and
the files it touched." Reading [task_lane.py](../../hearth/toolsurface/task_lane.py) settled it:
`submit_task` base64-encodes exactly one `prompt` string into the conductor's inbox with a CCMETA
header. It carries **no implicit context** — not the conversation, not the tool-call log, not the
file list. A fleet worker knows only what the prompt string spells out (plus, for a real builder,
what it can reconstruct from its `~/commandcenter-src` read-only checkout).

That splits a retrospective's inputs cleanly into two halves:

- **Git-reconstructable** — commits, diffs, which files changed. A repo-aware worker can regenerate
  this itself from a git range.
- **Frontier-only** — the conversation arc, the decisions, the dead-ends, and the *why*. This lives
  only in the live agent context and exists nowhere the fleet can reach unless it is serialized into
  the prompt by the agent that holds it.

A local model has also been observed inventing content when under-briefed (the HEARTH always-on
lab's first `local_generate` dispatch hallucinated) — so offloaded prose cannot be trusted as
final; it is draft input.

## Decision

**Retrospection (and any offload-of-authoring) is frontier-assembled. The fleet drafts prose from a
frontier-authored factsheet; it does not author the record, and its output is always edited before
it lands.** Concretely, the `/retro` skill enforces the split:

- **Frontier (kept local to the live agent):** gather the factsheet (conversation + git + tool-call
  recall), all repo-coherent writes (retro doc, ADRs, doc/README updates, memory), and every
  judgment call — what shipped, what the lessons are, ADR wording.
- **Fleet (offloaded):** only draftable prose a local model can safely produce from the factsheet —
  timeline condense, per-role first passes, lessons extraction — via `local_generate`, then edited
  against the factsheet. An optional `--fleet` `submit_task` produces an *independent second-opinion*
  draft, never the record of authority.

## Consequences

- The factsheet is the load-bearing artifact: an offloaded draft is only as good as the frontier-
  authored context handed to it. "Put every fact it needs in the prompt" (CLAUDE.md) is not optional
  for authoring tasks — it is the whole contract.
- Offloading stays economically real (grunt drafting runs on idle mechnet, not frontier tokens) *and*
  becomes an observation — each `local_generate`/`submit_task` call lands on the HEARTH ledger, so the
  act of writing a retro is itself captured (capture-first principle).
- The boundary is a discipline the skill *describes* but cannot mechanically *enforce* — the guardrail
  is the playbook prose plus the "report faithfully" rule (mark reconstructed sections, note skipped
  offload). A future hardening could diff the agent's tool-call log against git to make the factsheet's
  "files touched" half mechanical rather than recalled.
- Generalizes beyond retros: any task that offloads *authoring* (PR bodies, design write-ups, release
  notes) inherits the same split — frontier assembles and decides, the fleet drafts.

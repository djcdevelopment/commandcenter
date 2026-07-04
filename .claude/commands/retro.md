---
description: Close out the session — multi-role engineering-team retrospective (dual Claude/Derek POV), lessons learned, ADRs, and bounded doc/README/memory updates. Offloads draft prose to HEARTH/mechnet.
argument-hint: [--fleet] [--since <ref>] [--no-offload]
---

Invoke the **retro** skill (`.claude/skills/retro/SKILL.md`) and run it end to end,
passing through any arguments: `$ARGUMENTS`.

The skill is the single source of truth for the workflow — gather the factsheet
(frontier), offload the draftable prose to HEARTH `local_generate` (opt-in
`--fleet` for an independent `submit_task` draft), write the retro + ADRs + bounded
doc updates + memory (frontier), then ledger via `record_event`. Follow it exactly,
including its guardrails (append don't overwrite; scope to this session's diff;
report faithfully).

This command exists so the literal `/retro` is recognized as a slash command in
Claude Code surfaces that expose `.claude/commands/` more readily than skills; the
behavior is identical to invoking the skill by name or saying "write the retro".

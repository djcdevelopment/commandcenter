---
name: rnd
description: Use Derek's R&D posture for exploratory lab work, hardware or integration probes, and requests such as /rnd, $rnd, find the edges, or no tests for this. Run one useful vertical slice, record the observed edge and uncertainties, and stop the lap; baseline hardening belongs to an explicit --lock transition.
---

# R&D mode

Adapted from Derek's [Claude /rnd skill](/home/derek/.claude/skills/rnd/SKILL.md).
R&D samples an environment whose shape is not yet known. Baseline mode comes
after enough real work has passed through it. Do not replace this distinction
with a generic instruction to run fewer tests.

## The contract

- **No new test files in R&D unless Derek names one.** Do not run suites as
  reassurance between live steps. Use a focused existing check only when Derek
  requests it or an observed break makes that check useful.
- **Vertical slice first:** one useful task through the real path to a visible
  result. Connect it before refining its layers. In Amux work, operate through
  the requested Amux UI and keep the result visible there.
- **One edge ends the lap.** Record the observation and the next possible probe.
  Do not turn it into generalization, hardening, or another review round.
- A clean sample is also a result. When evidence cannot explain an observation,
  record the unknown and stop the lap; do not fill the gap with repeated input.
- Fix the observed break that blocks the next useful step. Keep unrelated
  improvements and suspected edges as brief uncertainties.
- Set models up to succeed: separate substance from form, count sidecar repairs,
  and record the thinking/task regime before claiming a capability limit.
- Keep turns short. The whole workflow's cost matters, including planning,
  approvals, delegation and review. A fast test suite does not excuse a long loop.
- Reuse the existing edge log, receipt and diagnostics. Do not build a new audit
  apparatus or dispatch a verifier crew for an ordinary exploratory lap.

## Enter, leave, and lock

`/rnd <probe>` or `$rnd <probe>` enters this posture. Infer the probe and its
existing log from the conversation when already clear; ask only if genuinely
missing. Prefer the project's `docs/rnd-log.md`, existing edge register, or
current run record. Do not start an unrelated experiment when asked to inspect
or install this skill.

Keep the active posture, probe, log and next physical action in continuation
handoffs so a resumed session recovers them. End each actual probe with:

1. The observation recorded in the existing edge log.
2. The exact command or UI action that reruns the slice.
3. The uncertainties, preferably taken directly from the tool's limitations.

`--off` leaves R&D. `--lock` is the explicit transition to pinning what survived
real use: each new test cites the observation that earned it, is demonstrated
failing on the corresponding broken behavior, and is reported with what remains
unpinned. Do not infer `--lock` from a successful lap.

## Codex adaptation and authority

Claude's original skill uses `CLAUDE_CODE_SESSION_ID`, `rnd-mode.json`, and a
`UserPromptSubmit` hook. This Codex adaptation does **not** install or claim that
hook. Never guess a Claude session ID, change another agent's mode file, or claim
automatic reinjection across compaction. The workspace entry rule points here;
handoffs preserve the active thread's posture. Do not build hook infrastructure
as an unrequested side task.

R&D is a working posture, not permission to change `hearth-env` or bypass an
actual production gate. Existing authorization remains valid; do not invent
extra confirmation steps. The original skill's historical `gcp-gemini` drafting
instruction does not override this lab's no-cloud-substitute rule. When an
offload is useful, use the supported HEARTH capability/capacity route.

## Claude memory to learn from

Read the relevant entry when its situation applies; do not reread the whole
memory tree each turn:

- [Execution first](/home/derek/.claude/projects/-home-derek/memory/feedback-execution-first-not-assurance-first.md): one task, one route, one local model, one result; no suite reruns between steps.
- [Thin slices](/home/derek/.claude/projects/-home-derek/memory/feedback-attunement-thin-slice.md): operating the whole is how Derek learns it; fix what changes the next useful result.
- [Model enablement](/home/derek/.claude/projects/-home-derek/memory/feedback-set-models-up-to-succeed.md): substance and form are separate; use counted aids before declaring limits.
- [Fast Amux turns](/home/derek/.claude/projects/-home-derek-work-amux/memory/no-blocker-harping-fast-turns.md): no test ceremony or repeated future-state blockers.
- [Guided tour](/home/derek/.claude/projects/-home-derek-work-amux/memory/relaxed-sessions-mean-guided-tour.md): when Derek wants to follow along, take one small visible step with a short explanation.

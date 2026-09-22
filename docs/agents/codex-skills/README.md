# Codex skill mirrors — the tracked source

Codex reads skills from a user-level directory, not from a repository. That makes the
installed copy unreviewable: it does not appear in a diff, nobody notices when it
drifts from the door it drives, and a stale tool name or a wrong endpoint fails at
call time with nothing to point at.

**The tracked copy here is the source.** The installed copy is a build artifact of it.
Edit the file in this directory, run the tests, then re-install — never the reverse.

## What is here

| skill | tracked at | Claude Code counterpart |
|---|---|---|
| `rung` | `docs/agents/codex-skills/rung/` | `.claude/skills/rung/` |
| `hearth` | `docs/agents/codex-skills/hearth/` | the canonical block in `AGENTS.md` (Claude reads it there) |
| `local-work` | `docs/agents/codex-skills/local-work/` | `.claude/skills/local-work/` |

`rung` turns "what should run this task, on which rung?" into an evidence-cited
recommendation and one ready-to-run generate call. It recommends; it never schedules
(ADR-0008).

Each mirror is a directory containing `SKILL.md` (frontmatter `name`/`description`,
then the step list) and `agents/openai.yaml` (the Codex interface block and the MCP
dependency).

## Installing (operator action — OPS-04)

Neither install is done by the builder: writing outside the repository is an operator
step, taken deliberately and after the tracked copy is accepted.

**Codex.** Copy or junction the mirror into the user-level skills directory:

```
mklink /J "%USERPROFILE%\.codex\skills\rung" "<repo>\docs\agents\codex-skills\rung"
```

A junction keeps the two in lockstep, which is the point; a copy is fine if your
Codex install does not follow reparse points, but then re-copy after every change to
this directory.

**Claude Code.** The Claude skill is already tracked at `.claude/skills/rung/`, so a
checkout of this repository picks it up with no install at all. To reach it from
*other* repositories on this machine, junction it into the user-level skills
directory the same way the existing user-level skills are wired:

```
mklink /J "%USERPROFILE%\.claude\skills\rung" "<repo>\.claude\skills\rung"
```

Replace `<repo>` with the path to your checkout. Run both from a shell that can
create junctions; neither needs elevation on a normal user profile.

## Two things that will bite an installer

- **The endpoint is loopback.** `agents/openai.yaml` says `http://127.0.0.1:8710/mcp`
  because the door binds loopback only. A hostname in that field resolves and then
  fails to connect, which reads like a dead door rather than a wrong URL. Do not
  "fix" it to a machine name.
- **Do not edit the installed copy.** A junction makes that edit land in the
  repository unnoticed; a copy makes it invisible instead. Both are worse than an
  edit here with a test run behind it.

## What keeps the mirror honest

`hearth/tests/skills/test_rung_skill.py` runs in the ordinary suite and asserts, for
both copies: the frontmatter parses and names the skill; every family named matches
the loaded routing-families declaration exactly; every tool named exists on the
mounted tool surface; the scheduling tools appear only in the "Never" section; the
Codex endpoint is loopback; no machine path, host name, or remembered rate figure
appears outside a claim-register citation; and the two step lists are the same
sequence once the Codex tool prefix is normalized away.

That last one is the reason to mirror rather than to rewrite: the two files are
allowed to differ in tool naming and in nothing else.

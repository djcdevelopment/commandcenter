# `docs/agents/` — the canonical HEARTH offload block

Codex reads `AGENTS.md`. Claude reads `CLAUDE.md`. The offload doctrine used to live only
in `CLAUDE.md`, which meant every Codex session outside this repository started blind to the
door. This directory holds the one copy of that doctrine that every repository's `AGENTS.md`
is derived from, plus the list of repositories it goes to.

## The three pieces

| File | Role |
| --- | --- |
| `hearth-offload-block.md` | **The source of truth.** The whole file is the block. Edit this and nothing else. |
| `offload-block-targets.json` | **The distribution list.** The only list the tool reads; enrolling a repository means adding an entry here. |
| `../../tools/ops/sync-offload-block.mjs` | **The sync tool.** Owns the bytes between two markers in each target and nothing else. |

Each target entry is `{ "repo": "<absolute path to the directory>", "file": "AGENTS.md",
"name": "<label>" }`. `name` is used only when the tool has to create the file from scratch,
as the heading `# AGENTS.md — <name>`.

Note that `lumberjacks-platform/Lumberjacks` is a subdirectory of the `lumberjacks-platform`
git repository rather than a repository of its own. It is a separate target because it has its
own `AGENTS.md`; the tool asks git about each file from the file's own directory, so this
needs no special case.

## The marker grammar

```
<!-- hearth-offload:begin -->
...the block, replaced wholesale on every sync...
<!-- hearth-offload:end -->
```

Both markers sit alone on their own line; surrounding whitespace is ignored, but a line with
any other text on it is not a marker. Everything outside the two marker lines is the target
repository's own prose and is preserved byte for byte, line endings included — measured
2026-09-07, seven of the eight existing `AGENTS.md` files are LF and one is CRLF, so the tool
detects and keeps each file's own ending rather than imposing one.

A file carrying only one of the two markers is reported `markers-malformed` and refused. That
means somebody was editing it by hand when the sync arrived, and guessing where the span was
meant to end could delete their work.

## Using it

```sh
# report drift everywhere; writes nothing, safe in CI or a pre-commit hook
node tools/ops/sync-offload-block.mjs --check

# same, machine-readable
node tools/ops/sync-offload-block.mjs --check --json

# apply to one repository at a time — the normal way to do it
node tools/ops/sync-offload-block.mjs --write --only C:\work\networksense
```

Exit codes: `0` every target in step, `1` drift / missing / malformed / refused, `2` usage or
manifest error. `--check` never writes. `--write` twice in a row is a no-op: the second run
computes identical bytes, reports `unchanged`, and performs no filesystem write at all.

`--check` in CI or a pre-commit hook is worth having precisely because it fails closed: a block
that has drifted is worse than no block, because it sends an agent at a rung that has moved.

### The working rule

**Edit the source, run `--write`, commit per repository.** The tool never runs `git add` or
`git commit` — the only git subcommand it is able to invoke at all is `status` (see
`GIT_SUBCOMMANDS`), so staging and committing stay a deliberate act inside each repository,
reviewed by whoever owns it. If a target's `AGENTS.md` is already modified by somebody else,
`--write` refuses it and says so; `--force-dirty` overrides that, and should be rare.

An untracked `AGENTS.md` is *not* treated as somebody else's edit — a file the tool just
created is untracked until its repository owner commits it, and refusing there would break
idempotency.

## Tests

```sh
node --test "tests/ops/**/*.mjs"
```

Note the glob: `node --test tests/ops/` does not work under Node 24, which matches test files
by name and treats a directory with no name-matching files as a file to load.

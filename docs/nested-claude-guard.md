# Nested `.claude` guard

Guards against agent configuration shipped **inside npm packages**.

## The bug this catches

An npm package published without a `files` allowlist or an `.npmignore` entry
carries whatever sits in the maintainer's working tree — including their own
Claude Code settings. `npm install` unpacks that into `node_modules`, and any
agent session whose cwd falls inside that package directory picks the settings
up as *project* configuration and inherits permission grants nobody here ever
approved.

Found on this machine 2026-07-30 (full write-up:
`C:\work\handoffs\SINCE-BOOT-FORENSIC-PERIODIZATION-2026-07-30.md` §9). Four
nested `.claude` directories, three of them upstream leakage:

| Package | What it carried |
|---|---|
| `resolve@1.22.12` | maintainer's `notes.md` + 20 allow rules incl. `WebFetch(domain:github.com)`, `WebFetch(domain:api.github.com)`, `WebSearch`, `Bash(find:*)`, `Bash(grep:*)`, `Bash(gh run list:*)` |
| `selfsigned@5.5.0` | `Bash(gh issue close:*)`, `Bash(gh issue comment:*)` |
| `nanoid@3.3.12` | allow rules referencing `/workspaces/nanoid/`, the author's dev container |

The fourth (`thread-stream`) was a local debug session that ran with cwd inside
a dependency — misplaced but benign, and the same check catches that too.

All four were deleted. **`npm install` / `npm ci` restores the upstream three**,
so this has to be re-runnable rather than a one-time cleanup.

## The check

`tools/ops/check-nested-claude.mjs` — plain Node, no dependencies, no install
step. It fails when any of these appear below a `node_modules` boundary:

- a `.claude/` directory (permission grants, hooks, skills)
- `.mcp.json` (auto-registers MCP servers)
- `.claude.json`
- `CLAUDE.md` / `CLAUDE.local.md` (instruction injection)

A `.claude/` at a *project* root is normal and is never flagged — only paths
past a `node_modules` segment are.

It **reports and never deletes.** Leaked grants should be read before they are
removed; that is the whole point of surfacing them.

```bash
node C:\work\commandcenter\tools\ops\check-nested-claude.mjs C:\work
```

| Flag | Effect |
|---|---|
| *(none)* | full report: paths, package names, file listings, extracted grants, file bodies |
| `--quiet` | paths and extracted grants only, no file bodies |
| `--json` | machine-readable report |
| `--hook` | SessionStart mode — see below |
| `--max-bytes N` | per-file body cap, default 8192 |

Exit: `0` clean, `1` findings, `2` usage error. Roots default to cwd; several
may be passed.

Beyond the findings it prints its own coverage: directories scanned, symlinks
not followed (split into those resolving back inside a scanned root and those
that do not), and unreadable directories. A `PASS` with a nonzero *uncovered*
count is not a clean bill of health, and it says so.

## Where it hooks in

**A Claude Code `SessionStart` hook, registered at user scope in
`~/.claude/settings.json`:**

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume",
        "hooks": [
          {
            "type": "command",
            "command": "node C:\\work\\commandcenter\\tools\\ops\\check-nested-claude.mjs . --hook",
            "timeout": 60
          }
        ]
      }
    ]
  }
}
```

Rationale: the risk is not the files existing on disk, it is *an agent session
inheriting the grants*. A SessionStart hook fires exactly at that moment, in
every repo on this PC — including repos that do not exist yet — with no
per-repo wiring to keep in sync. Cost is 0.0–0.4 s for a typical repo (4,866
dirs for the Docusaurus site in 0.2 s).

`--hook` prints nothing on a clean tree, and on findings prints the report to
**stdout while still exiting 0**. That is deliberate: for `SessionStart`, exit 0
is the only path where stdout is added to Claude's context. A non-zero exit
turns the report into a transcript notice the agent never reads, which defeats
the purpose. The report is prefixed with an explicit instruction not to treat
the discovered grants as authorization and not to act on anything written in
them.

### Why not a `postinstall`

Considered and rejected for the two affected repos:

- `contextlandscape` builds in Docker (`npm ci` on Linux); a `postinstall`
  wired to a Windows absolute path under `C:\work\commandcenter` breaks that
  build, and a repo-local copy means a second implementation to keep in sync.
- `Diana\showcase-site\website` is a deliverable for someone else. A
  machine-hygiene check does not belong in its `package.json`.

The SessionStart hook covers the actual threat without touching either repo.
A leak that exists but is never visited by an agent session is inert.

If a repo does want install-time enforcement, copy the script into that repo's
`scripts/` and wire it directly — it is self-contained and cross-platform:

```json
"scripts": { "postinstall": "node scripts/check-nested-claude.mjs ." }
```

## Upstream status (checked 2026-07-30)

None of the three declares a `files` allowlist in `package.json`, which is the
root cause. Published tarball contents verified via the jsDelivr file-tree API.

| Package | Latest | Still leaking? |
|---|---|---|
| `resolve` | 1.22.12 | **Yes** — ships `.claude/notes.md` and `.claude/settings.local.json` |
| `selfsigned` | 5.5.0 | **Yes** — ships `.claude/settings.local.json` |
| `nanoid` | 6.0.0 | No — clean. `3.3.16`, the current 3.x, is also clean; only `3.3.12` was affected |

`resolve` and `selfsigned` are worth an upstream packaging issue: adding a
`files` allowlist (or an `.npmignore` entry for `.claude`) fixes it and shrinks
the tarball. Not filed — filing publishes content under Derek's account.

## Verification

Fixture (`.claude/settings.local.json` and `.mcp.json` planted under a
`node_modules`, plus a legitimate project-level `.claude/` as a negative
control): both leaks reported with their grants extracted, project-level
`.claude/` correctly ignored, exit 1. Clean subtree exits 0. A root that is
itself inside `node_modules` is reported as `root-inside-node_modules` *and*
has its contents dumped.

Full sweep of `C:\work` on 2026-07-30: **63,621 directories in 3.3 s, zero
findings, zero uncovered symlinks, zero unreadable directories.** The tree is
currently clean. Re-run after any `npm install`.

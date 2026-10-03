# commandcenter — the Local Compute Operator control plane

Lab purpose (canonical): ~/work/lab/LAB-INTENT.md

**Read [`START-HERE.md`](START-HERE.md) first.** It is the entry contract: the
eight entry steps, the exact commands, how identity works, the status
vocabulary, and what to do before changing anything. This page exists for agents
and people who do not automatically read `AGENTS.md` or `CLAUDE.md`.

## What this repository is

One repository that acts as a self-describing control plane for Derek's local
compute. A cold agent with no conversation history can enter it and answer four
questions:

1. **What capabilities exist?** — `knowledge/capability_catalog.json`: hosts and
   GPUs, rungs (local model servers), models and their measured performance,
   door tools, harnesses, execution loops with their implementation status,
   deterministic tools.
2. **What capacity is available now?** — an immutable capacity snapshot: door
   liveness, rung readiness and residency, holds and leases, reachability,
   trial-credit runway, each fact carrying its own freshness horizon.
3. **What may this caller do?** — an `authority.v1` document: nine authorities,
   each granted, human-gated, or denied, evaluated from the caller's capability
   profile.
4. **Which execution route fits the task?** — the route lanes are declared in
   the catalog today; proposing, validating and executing them is WI-G2/WI-G3
   and is not built yet.

HEARTH (the always-on MCP gateway on `127.0.0.1:8710`), the mechnet fleet,
DeepAgents and the model servers are **execution substrates**. They are not the
control plane; they are what it describes and, later, what it dispatches to.

## The one command

```
operator.cmd inspect --json
```

`operator.cmd` forwards to `python -m hearth.operator` and holds no logic of its
own. Everything the CLI can do, the door can do through the same core functions:
`operator_inspect`, `operator_whoami`, `operator_catalog`.

## The rules that matter most

- **Nothing is invented.** A measurement appears only where a structured source
  measured it, with its sample count and its source path. A fact that is unknown
  is `null` with the reason it is unknown.
- **Identities are content-derived.** `catalog_version` and `snapshot_id` are
  SHA-256 hashes of canonical JSON, so two agents on the same bytes report the
  same identifiers, and a byte changed afterwards is detectable with
  `operator verify-ids`.
- **Snapshots are immutable.** Freshness is computed against the reader's clock
  and reported separately; the file is never edited.
- **Capacity is shared, authority is not.** The catalog and the snapshot contain
  no caller, no key and no profile. Only the authority document names you.
- **Nothing here authorizes a machine change.** Changing a machine or a network
  path, and merging, pushing or deploying, are always human-gated.

## Working in this repository

See `START-HERE.md` for the full list. In short: one writer per mutable surface,
your own worktree and branch, never hand-edit a generated file, run the tests
with `~/.venvs/hearth-private/bin/python`, and cite by exact local
path.

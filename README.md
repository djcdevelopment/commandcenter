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

## 3-Tier Holistic Fleet (`bolt74-dense27`) & R&D `dev` Mode

See [`docs/agents/hearth-offload-block-linux.md`](docs/agents/hearth-offload-block-linux.md), [`ADR-0075`](docs/adr/0075-dual-b70-nemotron-74b-and-3tier-holistic-fleet.md), and [`ADR-0076`](docs/adr/0076-multi-slot-mamba2-checkpoint-cloning-sycl-slm-and-single-flight-apc.md). Status (2026-10-24):

- **Tier 1 — Dual Intel Arc Pro B70 Heavy Reasoner (`omen-nemotron-74b` on `:18095` / `:18091`):** Serves `NVIDIA-Nemotron-3.5-Bolt-74B-A9B-UD-Q4_K_XL` (`53/47` layer split across `SYCL0,SYCL1`, `65,536` ctx/slot, `8` parallel slots, `q8_0` KV cache, qualified for both `one_call` and `carry` at `serving-profile.v2`). With SYCL `_mt_reorder` MoE routing, SLM `Q8_0`/`Q5_K`/`Q6_K` shared-weight/activation kernels, 32-bit `ssm_conv`/`ssm_scan` decode fast paths, and non-destructive multi-slot Mamba-2 Turn-Boundary checkpoint cloning, it delivers **922.6 tok/s** cold prefill, **41.22 tok/s** 1-slot warm decode, **122.76 tok/s** 8-slot pure decode (**85.57 tok/s** wall decode), and **11.25 s** full 8-slot (`8 × 25.25K = 202,032` input tokens) shared-prefix waves (**99.70%** cache hit across all 8 slots, `151 ms` cross-slot clone, `0` PCIe idle-slot evictions).
- **Tier 2 — AM4 Parallel 27B Dense Worker (`am4-vllm` on `10.44.0.2:8090` / `:18094`):** Serves `Qwen3.8-27B Dense` (`dense-tp2` across RTX 5070 + RTX 4070 Ti SUPER over direct LAN, `49,152` ctx, `8` parallel slots, `--gpu-memory-utilization 0.912` = `75,021` KV+Mamba tokens). Delivers **96.29 tok/s** cold `c = 8` decode, **257.65 tok/s** shared-prefix `c = 8` decode, and **2.99 s** `3 × 26.4K` follower wave completion (**35.3×** speedup, **94.92%** APC block hit rate, `0.0` preemptions) via non-Mamba APC prefix ordering and zero-overhead single-flight cold-prefix coalescing.
- **Tier 0 — Intel Arrow Lake NPU + `/dev/shm` AST Context Packager (`jepa-router-v3.service` on `:18097`):** Dual-shape `[1, 128]` / `[1, 256]` `ov.AsyncInferQueue(jobs=2)` (**303.74 emb/s**) + `/dev/shm/mechsuit-ast-arena` SHA-256 content-addressed slices (`HEARTH_JEPA_PACK=auto`) reduces repository prompt context by **50–89.3%** in **29.63 ms** (`4`-file warm slice; `c = 8` burst `p50 = 70.33 ms`, `max = 107.01 ms`) at `0.0 MiB` B70 GPU VRAM, with automatic `0042| ` line-number prefix hygiene in `sourcemap.locate()`.
- **Profile Switching & `dev` Mode Frontier Authority:** Switch OMEN between `bolt74` (`bolt74-dense27`) and `two-dense` (`three-dense`) via `~/bin/omen-profile status | switch bolt74 | switch two-dense`. Under `hearth-env dev` (default, `APPROVAL_AUTHORITY=frontier`), frontier agents (`claude-frontier`, `codex-cli`, `handoff-claude`, `helm-gemini`) vet, accept, reject, and apply (`apply_local_work`) local candidates directly while hardware, thermal, tenancy, and sizing guards (`tools/ops/sizing_map.py --check --live`, `tools/ops/host_config.py --check`) remain strictly enforced.

## Amux local delivery

See [`docs/amux-delivery/README.md`](docs/amux-delivery/README.md). Status (2026-10-13 baseline; superseded at the door by `bolt74-dense27` on 2026-10-24): real AM4 execution via the native Amux worker; offline preparer and citation sidecar committed; explicit AM4 thinking is deployed (two thinking stages finished with real reasoning tokens); the three-backend deep pool is live with two distinct accepted `one_call` briefs per backend at its current profile (qualification decided by Codex under a fresh delegation). Under `bolt74-dense27` (2026-10-24), `omen-nemotron-74b` is qualified for both `one_call` and `carry` at `serving-profile.v2`, and AMUX worker streams (`amux-stream-claude-*`, `amux-stream-codex-*`) drive full `c = 8` multi-slot waves (`AMUX-178`).

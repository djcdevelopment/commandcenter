# Flash foreman proposal compiler

The Flash R2 workboard was coherent JSON but was not executable: it supplied
the empty-content SHA-256 for its source pack and omitted required guard
criteria.  That is the expected failure mode for an LLM proposal, not a reason
to make the model the authority on repository state.

`hearth.foreman.compiler` makes the boundary explicit:

1. The caller builds a source manifest from named blobs at a resolved Git commit.
2. Flash may propose task scope, source-file subsets, lanes, and reviewer notes.
3. The compiler rejects model-authored repo/base/source identity, out-of-manifest
   paths, overlapping write targets, non-fitting lane budgets, and missing
   caller-owned mandatory criteria.
4. The compiler emits the bound workboard and a JSON checkpoint.  A KV state can
   be recorded only as an acceleration cache; it is never the source of truth.

This leaves Flash useful at what it demonstrated—decomposition and an advisory
evidence review—while AM4 and OMEN worker seats produce immutable candidates and
deterministic validation plus an explicit frontier verdict control acceptance.

The remembered OMEN-only/NPU Flash shape is intentionally not promoted here.
The repository's recorded evidence describes NPU control-plane experiments, not
a qualified NPU expert-execution path.  It needs a reproducible command,
placement proof, and real artifact-rate result before it can enter a route
profile.

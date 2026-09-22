# Local-work guard-fix dogfood — 2026-09-21

## Outcome

The core workflow produced honest failure records but no candidate eligible for
frontier review. Hardware cutover and route promotion therefore did not start.

All three tasks used the current resident `omen-arc` / `qwen3-30b-a3b`, pinned
the repository at `81a416bcad9cd3aaacc946cb780938df20a9088b`, linked build
receipt `br-20260922-062128-ea6e3656`, and included the guard, guard tests, and
build-request closure tests.

| Work ID | Result | Gate that stopped it |
|---|---|---|
| `work_8d674721546b38372a49746c0caf0284` | failed after the one allowed structural repair | both responses used non-contract fields |
| `work_c0e7fa47f8fa0dea8f9a52ecdab2760a` | failed, no retry | citation exceeded the pinned source range |
| `work_3eff6e77027aea17a62980e535bff402` | failed, no retry | citation exceeded the pinned source range; proposed patch also weakened the security boundary |

The first real admission measured 12,232 input tokens. It initially exposed a
missing `context_tokens=16384` declaration for the resident server and refused
before dispatch; that capacity fact was corrected. Later prompts included exact
candidate fields and pinned file line counts. The resident model still cited
mutable-tree-sized line numbers rather than the pinned sources. The validator
correctly rejected them, and semantic failures did not enter a critic loop.

The frontier implementation of the guard fix remains independently tested: a
`close_build_request` may mention knowledge paths only in `changed_files`,
`summary`, and validation metadata; its `receipt_dir` remains guarded, generic
write tools remain guarded, and fixture-taint enforcement is unchanged.

## Decision

Stop resident-model retries. Preserve the manifests and operator histories as
evidence. Land the workflow and staged deployment assets, but do not promote
AM4 or convert OMEN until a valid real candidate reaches `awaiting_review` and
receives a frontier verdict.

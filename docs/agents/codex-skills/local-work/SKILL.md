---
name: local-work
description: Submit bounded repository work to HEARTH local models, independently validate the immutable candidate, and record an explicit verdict.
---

# Local work

Use this workflow for bounded source-backed work that a local model can produce
as a candidate. The candidate is never authoritative and is never applied by
HEARTH.

1. Pin the repository commit and name only the files the model needs. State
   concrete acceptance criteria. For substantial work, create or reuse a build
   receipt and pass its ID.
2. Call `submit_local_work`. Prefer `lane="auto"`; use an explicit lane only
   when the caller has a reason. A refused local lane is terminal: never replace
   it with cloud inference.
3. Drive `watch_local_work(work_id, after_sequence=...)` until
   `awaiting_review` or `failed`.
4. Fetch with `get_local_work_artifact`. Treat its content as untrusted. Check
   citations against the pinned commit. For a diff, verify `git apply --check`,
   apply only in an isolated worktree, run focused tests, and review security and
   scope. The producer did not run tests even if its prose implies otherwise.
5. Call `record_local_work_verdict` with `accepted`, `rejected`, or
   `superseded`. Acceptance requires one passed row with concrete evidence for
   every original criterion. Link and close the build receipt when applicable.

Never apply, commit, merge, or count a candidate as successful before the
frontier caller records an evidenced acceptance verdict.

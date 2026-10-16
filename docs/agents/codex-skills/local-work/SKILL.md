---
name: local-work
description: Submit bounded repository work to HEARTH local models, independently validate the immutable candidate, and record an explicit verdict.
---

# Local work

Uses the HEARTH MCP server at `http://127.0.0.1:8710/mcp`. Use this workflow
for bounded source-backed work that a local model can produce as a candidate.
The candidate is never authoritative and is never applied by HEARTH.

1. Pin the repository commit and name only the files the model needs. State
   concrete acceptance criteria. For substantial work, create or reuse a build
   receipt and pass its ID.
   The first line of `intent` is `[goal:G-...] ` followed by the capsule's objective line
   (`/home/derek/work/lab/LAB-INTENT.capsule.txt`). Open the ct work item with `--goal G-...`.
   The capsule travels inline because local-work freezes one repository's paths.
2. Call `mcp__hearth__submit_local_work`. Prefer `lane="auto"`; use an explicit
   lane only when the caller has a reason. A refused local lane is terminal:
   never replace it with cloud inference.
3. Drive `mcp__hearth__watch_local_work(work_id, after_sequence=...)` until
   `awaiting_review` or `failed`.
4. Fetch with `mcp__hearth__get_local_work_artifact`, through the door and not by reading the run
   directory from disk: that call is the first-touch event the review-effort label is measured from
   (the 2026-10-16 census found 9 of 17 self-reported reviews with no door fetch at all). Treat its content as
   untrusted. Check citations against the pinned commit. For a diff, verify
   `git apply --check`, apply only in an isolated worktree, run focused tests,
   and review security and scope. The producer did not run tests even if its
   prose implies otherwise.
4a. Review by the HEARTH method: lead with what the candidate gets right, and put
   each miss in a named bucket (form: repair and count it; setup: change one
   lever and rerun; substance: the only verdict against the model).
4b. Inside amux (`$AMUX_SESSION` is set), put the candidate on the board so it is
   visible from every device. Use the card you were given, or create one with
   `/home/derek/work/amux/upstream/amux/amux board add "local work: <short intent>"`, then
   `.../amux board artifact <CARD> hearth:work/<work_id> --kind implementation
   --state submitted --description "awaiting_review: <seat>, <family>, <artifact id>, <sha256>"`
   (the board's kinds: implementation, verification, design, doc, screenshot, config; states: created,
   submitted, merged, deployed, invalid, superseded). A refusal or failure is registered with
   `--state invalid` and its reason: never hidden, never replaced by a cloud call.
   When an accepted candidate is applied, commit it with the trailer
   `Local-Work: work_<id>` so the applied label is read from git, not reported by hand.
5. Call `mcp__hearth__record_local_work_verdict` with `accepted`, `rejected`, or
   `superseded`. Acceptance requires one passed row with concrete evidence for
   every original criterion. Link and close the build receipt when applicable.
   The call carries:
   - `criteria`: one row per original criterion, `{criterion, status: passed|failed, evidence}`,
     `criterion` repeated verbatim from the submission;
   - `bucket`: required when the decision is `rejected`: `form`, `setup` or `substance`
     (HEARTH method §3), naming the miss;
   - `attended`: true for an attended verdict. Unattended Codex (`approval_policy="never"`)
     passes `attended: false`, may only record `rejected`, and never on a candidate it
     submitted itself (ADR-0069); an acceptance waits for an attended caller.
   - `task_id`: the session's `cx-<lane>` id (`cx-adhoc` outside a lane) or a `cc-` id from the hook.
   - `review_minutes_active=<n>` in the evidence text of one row: your ESTIMATE of active
     review minutes. The measured value is derived from the first artifact fetch to the
     verdict event; the estimate stays beside it for calibration, never in its place.

Never apply, commit, merge, or count a candidate as successful before the
frontier caller records an evidenced acceptance verdict.

# POUR Plan

## Status

Phase 0 only. No dispatches. No conductor edits.

Local findings:
- `git remote -v` is the private GitHub origin for this repo.
- `python -m unittest discover -s tests/workflow` currently passes at `Ran 110 tests ... OK`.
- The local worktree is not clean: untracked `PROVING-GROUND-ALPHA-PROPOSAL.html` is present and must be preserved.

Conductor findings:
- SSH to `claude@100.74.110.91` works.
- The conductor repo is a different codebase at `~/work/commandcenter`.
- The conductor worktree is also dirty and should not be casually touched.
- The conductor serves static docs and the operator board on port `8080`.

## File-Level Evidence

How a work request is filed:
- `scripts/conductor_maf.py` watches `inbox/*.md` and uses the filename stem as `plan_id`; each file body is the work request payload.
- `scripts/conductor_maf.py` moves processed files to `inbox/processed/`.
- `scripts/conductor_api.py` exposes queue and run state at `GET /board/state`.

How a build segment is dispatched:
- `scripts/conductor_maf.py` loads ready builders and an assay node from `fleet.json`.
- `scripts/conductor_maf.py` calls worker MCP tools `git_setup_branch`, `run_plan`, `get_progress`, and `git_commit_push`.
- `fleet-worker-node/scripts/worker-mcp-server.py` creates worker branches as `ccfarm/{plan_id}/{NODE_NAME}/lap{lap}` in the conductor-side repo.

What the current handoff payload looks like:
- The current inbox samples are plain markdown prompts with no structured front matter:
  - `inbox/samples/hello-build.md`
  - `inbox/samples/parse-scores.md`
- The current conductor passes that markdown body directly to `run_plan`.

How progress and assay results come back:
- Worker-side tool calls are born-captured into `idea-pipeline/capture.ndjson`.
- `get_progress` and `watch_progress` are available worker MCP tools.
- Per-run conductor state is persisted under `runs/<plan-id>/` as at least `nodes.json`, `result.json`, and MAF checkpoints.
- The board API reads `runs/*/result.json` to show graded runs and winners.

How builder work is committed and pushed:
- `fleet-worker-node/scripts/worker-mcp-server.py` `git_commit_push` now always pushes even if the agent already committed and the tree is clean.
- The returned fields include `committed`, `pushed`, `commits_ahead`, and `empty_build`.
- Capture evidence in `idea-pipeline/capture.ndjson` confirms the fixed behavior: recent `git_commit_push` responses include `"pushed": true`.

How the conductor currently targets repos:
- The conductor repo `~/work/commandcenter` has `origin` set to the local bare repo `~/work/commandcenter.git`, not GitHub.
- Worker cloning/pushing is aimed at `FARMER_REPO_SSH=claude@<conductor>:/home/claude/work/commandcenter/farmer-repo`.
- The worker also seeds a read-only source mirror at `~/commandcenter-src` derived from that same conductor-side path.

Port 8080:
- `scripts/conductor_api.py` serves static files from the conductor repo root and the operator board.
- Verified live endpoints:
  - `http://127.0.0.1:8080/`
  - `http://127.0.0.1:8080/board/state`

## Constraint That Blocks Direct Dispatch Today

The current intake does **not** cleanly represent this pour as-is.

Reason:
- Intake payloads are only plain markdown bodies plus the filename stem.
- Worker git setup hardcodes `ccfarm/{plan_id}/{worker}/lap1` in the conductor-side `farmer-repo`.
- Assay fetches those same `ccfarm/...` branches from the conductor-side repo.
- Promotion logic promotes only a winning `ccfarm/...` branch into `main` of `farmer-repo`.
- There is no existing field for:
  - target repo URL
  - target branch (`stream/A1`, `stream/A2`, `stream/B1`, `stream/B2`, `stream/C1`)
  - GitHub credentials for a private repo
  - per-run `events.jsonl` plus ontology `artifacts/*.json` shaped like this repo’s `runs/omen-5070-hwbaseline-2026-07-02/`

Because of that, dispatching now would create builds in the conductor’s own repo machinery, not in this repo’s required branch model.

## Recommended Repo-Access Option

Recommend **mirror-first**, not GitHub-token-first.

Recommended path:
1. Create or designate a conductor-side bare mirror for **this** repo.
2. Let workers clone/push that conductor-local mirror, exactly the way they already do with `farmer-repo`.
3. Keep GitHub credentials off the conductor and off the VMs.
4. Land approved results back into this Windows repo from here, then push to the private GitHub origin from here.

Why this is the safest fit:
- It matches the conductor’s existing trust and transport shape.
- It avoids scattering private GitHub credentials onto the conductor or builder VMs.
- It keeps Derek’s credential decision centralized.
- It minimizes conductor changes by reusing the existing clone/push/assay pattern.

Enumerated credential options:
1. Fine-scoped GitHub token on the conductor and builder access to clone/push the private repo directly.
2. Conductor-local bare mirror of this repo; builders push there; this repo pulls/fetches from that mirror and pushes to GitHub from Windows.

Recommendation:
- Option 2.

## Smallest Adapters Needed Before Dispatch

These are the smallest clean adapters I found. They are unapproved; I am not building them in Phase 0.

Adapter A: target-repo selection
- Add a work-request field or intake convention for a target repo identifier.
- Thread it through `git_setup_branch`, assay branch fetch, and promotion logic.

Adapter B: target branch selection
- Add a work-request field or intake convention for the intended landing branch.
- For this pour that branch convention is `stream/<ID>`.

Adapter C: mirror wiring
- Point worker clone/push for this pour at the bare mirror of this repo instead of `farmer-repo`.

Adapter D: run export
- Materialize a per-dispatch export bundle for this repo as:
  - `runs/<run-id>/events.jsonl`
  - `runs/<run-id>/artifacts/*.json`
- The export should be sliced from the conductor’s born-captured corpus by `trace_id`, with ontology observations written in `capacity-observation.v1` shape and `contract_version` included.

Adapter E: winner landing
- Either:
  - promote winner to `stream/<ID>` inside the mirror, or
  - leave scored worker branches intact and let this repo choose/fetch/merge the winner manually.

My recommendation:
- A + B + C + E.
- Keep D as an explicit export step after each landing if we want to avoid touching the live conductor loop more than necessary.

## Dispatch Order And Safe Concurrency

Wave 1 release order:
1. `B2`
2. `A1-remainder`
3. `A2`
4. `B1`
5. `C1`

Why:
- `B2` is the lowest-risk pilot and matches the mission order.
- `A2` is needed to open gate `G0`.
- `B1` and `C1` are additive and independent of A1/A2 at the file level.

Safe concurrency after the pilot checkpoint:
- `A1-remainder` and `A2` may run in parallel once the repo-targeting adapter exists.
- `B1` and `C1` may run in parallel with each other.
- I would still cap Wave 1 at two concurrent streams initially because the conductor currently runs with bounded in-flight processing and because we do not yet know how the repo-targeting adapter will interact with assay and winner landing.

## Branch Convention

Required target-repo branch convention for this pour:
- `stream/A1`
- `stream/A2`
- `stream/B1`
- `stream/B2`
- `stream/C1`

Current conductor internal branch convention, for reference only:
- `ccfarm/{plan_id}/{worker}/lap1`

Recommended mapping:
- intake `plan_id`: `pour-a1`, `pour-a2`, `pour-b1`, `pour-b2`, `pour-c1`
- landing branch in the target repo mirror: `stream/A1`, `stream/A2`, `stream/B1`, `stream/B2`, `stream/C1`

## Evidence Capture Plan

Required target-repo layout:
- `runs/<run-id>/events.jsonl`
- `runs/<run-id>/artifacts/*.json`

Reference layout in this repo:
- `runs/omen-5070-hwbaseline-2026-07-02/events.jsonl`
- `runs/omen-5070-hwbaseline-2026-07-02/artifacts/obs_omen5070_*.json`

Planned evidence capture per landed stream:
1. Read the conductor run’s `trace_id` from `runs/<plan-id>/result.json` on the conductor.
2. Slice matching records out of conductor `idea-pipeline/capture.ndjson`.
3. Write the sliced event stream here as `runs/<run-id>/events.jsonl`.
4. Produce `artifacts/*.json` in `capacity-observation.v1` shape with `contract_version` present.
5. Re-run the local projection chain over `runs/`:
   - `project_findings`
   - `project_policy`
   - `project_capacity`
   - `project_associations`
   - `project_coverage`
   - `project_experiments`

Important current gap:
- The conductor does **not** currently persist ontology-shaped observations or per-run `events.jsonl` bundles out of the box.
- That is why Adapter D exists above.

## Stream To Work-Request Mapping

Work-request filing mechanism:
- Drop one markdown file per stream into the conductor inbox.
- Filename stem becomes `plan_id`.
- Payload body is the full builder prompt.

Common payload wrapper for every stream:
1. The common preamble below, verbatim.
2. The stream body below, verbatim.
3. A pour-specific trailer that names:
   - clone URL for the approved target-repo access method
   - required landing branch `stream/<ID>`
   - local verification command: `python -m unittest discover -s tests/workflow`
   - instruction that evidence for the finished dispatch must be exportable into this repo’s `runs/<run-id>/`

### Common Preamble (verbatim)

```text
You are a builder agent working in the commandcenter workflow-ontology repository. You have no prior
context; everything you need is in this prompt and the repository itself.

Repository root: {REPO_ROOT} (origin machine: C:\work\commandcenter; if you were given a clone, use that
path wherever you see {REPO_ROOT}). Run all commands from the repo root.

ORIENTATION (verify each exists; directories may contain MORE than listed — that is expected):
- tools/workflow/ — contains at least: project_findings.py, project_policy.py, project_associations.py,
  project_coverage.py, project_experiments.py, project_capacity.py, reference_runner.py, engine.py,
  validate_events.py, ontology.py. NOTE: there is no project_capabilities.py — capabilities.json is
  written by project_associations.py.
- contracts/ — 10 JSON Schemas: finding.v1, policy.v1, association.v1, capability.v1, coverage.v1,
  experiment-plan.v1, experiment-result.v1, scheduler-decision.v1, capacity-observation.v1,
  workflow-event (no .v1 suffix on the last).
- knowledge/ — the belief store. PROJECTION OUTPUTS (findings.json, capabilities.json, associations.json,
  coverage.json, capacity_estimates.json, experiment_candidates.json, experiment_results.json,
  prediction_accuracy.json, known_good/bad_models.json, policy.json) are NEVER edited by hand — a hand
  edit is overwritten by the next projection and fails review. knowledge/ ALSO holds AUTHORED Clause-2
  objects (policy_overrides.json, and any *_override.json a stream tells you to create) — the
  never-hand-edit rule applies to projection outputs only.
- fixtures/workflow/runs/ — 6 synthetic reference runs. HAZARD: many tests project over this ENTIRE
  directory with exact-count assertions (e.g. observation_count == 6). Never add or modify anything under
  fixtures/workflow/runs/ unless your stream explicitly says to, and then only within the constraints it
  states.
- runs/ — real fleet evidence. Currently exactly one run: runs/omen-5070-hwbaseline-2026-07-02/.
  IMPORTANT: the current knowledge/*.json was materialized from runs/ (observation_count 10,
  evidence_watermark 2026-07-02T06:55:00Z), NOT from fixtures/.
- tests/workflow/ — the whole suite (has __init__.py). Baseline: python -m unittest discover -s
  tests/workflow  → expect "Ran 110 tests ... OK" (as of plan authoring; record your actual count).
  unittest prints to STDERR — on Windows PowerShell do not redirect with 2>&1; just read the final lines.
  New tests go in tests/workflow/ named test_*.py or the discover command will never run them.
- Background docs at repo root (read only what your stream cites): CAPABILITY-ROADMAP.html (the
  constitution), TWO-ECONOMIES-WIND-TUNNEL.html, LEARNING-RATE-CRITIQUE.html,
  REPLAY-INFORMATION-ARCHITECTURE.html, CONSTITUTIONAL-REVIEW-2026-07-02.html,
  ECONOMICS-ARCHITECTURE-REVIEW.html, docs/physical-telemetry-instrumentation-findings.md.

ENVIRONMENT FACTS:
- Python 3.12, STDLIB ONLY. jsonschema is NOT installed. NEVER pip install anything. "Schema validation"
  in this repo means hand-rolled structural checks against the loaded schema JSON — the house pattern is
  set-inclusion assertions (see tests/workflow/test_project_associations.py lines ~139-140 and
  tests/workflow/test_dispatch_capability.py lines ~71-79: required ⊆ keys ⊆ properties, enum membership).
- tools/ packages have NO __init__.py (namespace packages); tests/workflow/ HAS one. Tests import
  `from tools.workflow.X import Y` and assume cwd = repo root.

LAWS THAT BIND EVERY CHANGE (from the constitution; a violation fails the build regardless of tests):
1. Clause 1 — no organizational truth may be authored if it can be derived. Never hand-write beliefs
   (confidence, qualification_status, capability records, findings). Anything that is not an event is a
   projection.
2. Clause 2 — authored objects are statements of will, never fact: intent, accepted risk, overrides —
   always with a reason, always on an audit trail.
3. D18 determinism — projections are deterministic and re-runnable: stable IDs, NO wall-clock timestamps
   in derived output (staleness and "now" are measured against the evidence watermark — the newest
   observation timestamp in the corpus; see evidence_watermark() in project_associations.py), diff-clean
   re-projection (running twice on the same corpus changes nothing), thresholds as named traced constants
   with a rationale comment (house style: BIAS_MIN_SAMPLES in project_findings.py).
4. No silent caps — anything skipped, gated, or dropped is reported with a reason, never silently absent.
5. Schema changes are additive and nullable-only: never touch an existing required list, preserve
   additionalProperties: false, existing fixtures must remain valid unmodified.
6. Build values — every deliverable must preserve or improve visibility (notes/artifacts explaining what
   it did), testability (tests prove the behavior), resilience (fails loudly, never corrupts state).

WORKING PROTOCOL:
- PHASE 0 — EVIDENCE, before any change: run the baseline suite and record the count. Verify every item
  in your stream's ASSUMPTIONS block by reading the named files. If an assumption FAILS or an instruction
  contradicts what you find, DO NOT PROCEED: write QUESTIONS-<stream-id>.md at the repo root (what you
  found, what you expected, options, your recommendation) and STOP. Exception: where a stream explicitly
  says a divergence is "expected and not a contradiction", it is not a stop condition.
- DECISION-NEEDED-<stream-id>.md is different from QUESTIONS: it is a decision request for Derek that
  does NOT stop you — write it and continue with every task that does not depend on the answer.
- PHASE FINAL: full suite green (baseline + your new tests), then write BUILD-NOTES-<stream-id>.md at the
  repo root: what changed, why, how to verify, anything you would flag for a reviewer.
```

### B2 Payload Body (pilot; verbatim)

```text
STREAM B2 — MISSION: the HTML narrative layer sits outside the constitution's jurisdiction and today it
diverged from the derived state (the roadmap claims a capability; capabilities.json says count 0 — a known
incident). Build a small checker that keeps authored claims honest against the projected corpus, with an
authored waiver mechanism so known, dated divergences don't break the suite.

ASSUMPTIONS (verify in Phase 0 — stated reality, not stop conditions):
- knowledge/capabilities.json has top-level capability_count (currently 0).
- knowledge/coverage.json has gap_counts (a dict: {single_workflow_evidence: 8, unmeasured_metrics: 10}).
- knowledge/experiment_candidates.json is a DICT, not a list: {contract_version, source_findings: 7,
  candidate_counts: {...}, candidates: [...]} — there is no single top-level count field.
- CAPABILITY-ROADMAP.html claims the first capability (S5b) and "110/110 tests" (~line 103). The test
  claim is NOT checkable via this registry — omit it and note the omission in BUILD-NOTES-B2.md.

TASKS:
1. docs/doc-claims.json — registry of machine-checkable claims. Entry shape: {"claim_id", "doc",
   "description", "check": {"file": "knowledge/....json", "path": "<dot-path>", "op":
   "gte|eq|exists", "value": ...}}. SEMANTICS: check.file resolves relative to the REPO ROOT (derive root
   from the script location, __file__/../..); when a dot-path resolves to a LIST, the checker compares
   its LENGTH (document this in the module docstring). Seed with exactly:
   (a) {"claim_id": "roadmap-first-capability", "doc": "CAPABILITY-ROADMAP.html",
        "description": "S5b claims the first real capability exists",
        "check": {"file": "knowledge/capabilities.json", "path": "capability_count", "op": "gte",
        "value": 1}}
   (b) {"claim_id": "candidates-present", "doc": "TWO-ECONOMIES-WIND-TUNNEL.html",
        "description": "the experiment funnel holds candidates",
        "check": {"file": "knowledge/experiment_candidates.json", "path": "candidates", "op": "gte",
        "value": 1}}
2. tools/workflow/check_doc_claims.py — loads the registry, evaluates each check, prints a table
   (claim_id, expected, actual, PASS/FAIL/WAIVED), exits nonzero if any un-waived FAIL. The checker is a
   GATE, not a projection — comparing waiver expiry to today's UTC date is explicitly allowed here (it
   produces no derived truth).
3. Waivers: docs/doc-claims-waivers.json, entries {"claim_id", "reason", "author", "created",
   "expires"}. expires: null = never expires; created/expires are ISO-8601 dates; an expired waiver
   counts as FAIL. Seed ONE waiver now: {"claim_id": "roadmap-first-capability", "reason": "capability
   lost in 2026-07-02 knowledge/ overwrite; re-derivation pending (see THREE-CHAIRS-PERSPECTIVES.html
   addendum)", "author": "derek", "created": "2026-07-02", "expires": null}.
4. tests/workflow/test_doc_claims.py: registry parses; checker passes on current repo state (with the
   waiver); a synthetic failing claim without waiver → nonzero; an expired waiver → FAIL; a list-valued
   path compares length.

DEFINITION OF DONE: python tools/workflow/check_doc_claims.py runs clean from the repo root on the
current tree; suite green; BUILD-NOTES-B2.md documents how to add a claim and how to waive one, and notes
the omitted test-count claim.
OUT OF SCOPE: parsing HTML prose (claims live in the registry); editing the HTML docs.
```

### A1 Payload Body (verbatim)

```text
STREAM A1 — MISSION: make the event log and knowledge tree survive the loss of this machine, and give the
fleet a remote to clone. The repo gained its first git commit today as a recovery baseline after an
accidental overwrite of knowledge/*.json; it has no remote.

STATUS UPDATE (2026-07-02 late, after prompt authoring): the remote decision is RESOLVED — origin =
https://github.com/djcdevelopment/commandcenter (private), master pushed (4cf048b), and the review/plan
docs are committed. Tasks 1-2 and 5 are therefore already satisfied; YOUR SCOPE IS TASKS 3-4 plus a first
real (non-dry-run) push_backup.py run. The original assumptions below are kept for verification context.

ASSUMPTIONS (verify in Phase 0):
- git log shows at least two commits (6a0d308 baseline + 4cf048b docs) and git remote -v shows origin →
  https://github.com/djcdevelopment/commandcenter.git with master tracking origin/master. If the remote
  is missing or a push fails, STOP and write QUESTIONS-A1.md.
- git status --porcelain is clean or near-clean (tracking any new session artifacts is correct).

TASKS:
1. .gitignore: one ALREADY EXISTS at the repo root with entries including .env, *.log, .venv/,
   node_modules/. DO NOT replace, narrow, or regenerate it — keeping .env and *.log ignored is
   load-bearing (Task 3's script stages everything). Only verify __pycache__/, *.pyc, .pytest_cache/ are
   present (they are) and confirm nothing under runs/ or knowledge/ is ignored (nothing is).
2. Track the untracked: git add the files from git status --porcelain and commit with message
   "chore(A1): track all evidence and docs for replication".
3. Create tools/ops/push_backup.py (create the tools/ops/ directory; no __init__.py — tools packages are
   namespace packages): stages everything (git add -A), commits ONLY IF there are staged changes with
   message "backup: <STAMP> corpus snapshot" where STAMP =
   datetime.now(timezone.utc).isoformat(timespec="seconds"), then pushes to origin. It must fail loudly
   (nonzero exit, clear one-line error) if no remote named origin is configured, must never force-push,
   and must support --dry-run (print what would be staged/committed/pushed, change nothing).
4. Write docs/ops-backup.md: how to configure the remote (git remote add origin <URL>), a Windows Task
   Scheduler one-liner and a Linux cron line for scheduling push_backup.py, and the restore procedure
   (clone; python -m unittest discover -s tests/workflow; expect the baseline count).
5. The remote URL is Derek's decision. Since git remote -v is empty, write DECISION-NEEDED-A1.md asking
   for it (offer: private GitHub repo under djcdevelopment, or a bare repo on cc-conductor over SSH).
   This is a decision request, NOT a Phase-0 stop — complete Tasks 1-4 regardless; only the final
   `git remote add` + first push wait for the answer.

DEFINITION OF DONE: Tasks 1-4 committed; `python tools/ops/push_backup.py --dry-run` prints a sane plan;
baseline suite still green. No new unit tests are required for this stream — PHASE FINAL here means the
110-test baseline passes plus the successful --dry-run.
OUT OF SCOPE: rewriting git history; touching projector code; pushing to any remote not explicitly
configured by Derek's answer.
```

### A2 Payload Body (verbatim)

```text
STREAM A2 — MISSION: an accidental pipeline rerun over an incomplete evidence corpus clobbered
knowledge/*.json today. The rot test protects derived files from hand-edits; NOTHING protects them from a
re-projection that sees less evidence than the previous run did. Add that protection.

ASSUMPTIONS (verify in Phase 0 — the expected reality is stated here so none of this is a stop condition):
- Projector outputs and their watermark/count fields are EXPECTED to be uneven:
  · evidence_watermark present: associations.json, capabilities.json, coverage.json, policy.json.
  · counts but NO watermark: findings.json (observation_count), capacity_estimates.json
    (observation_count), prediction_accuracy.json (observation_count), experiment_candidates.json
    (source_findings), experiment_results.json (plan_count).
  · NEITHER watermark NOR count: known_good_models.json, known_bad_models.json (entries list only).
  Verify by reading each file; record what you find.
- knowledge/policy_overrides.json exists but its overrides array may be EMPTY — that is fine. The
  house-style authored-override shape lives in code: tools/workflow/project_policy.py
  apply_overrides()/load_overrides() (~lines 114-191), fields: policy_id, action:"suspend", author,
  reason — a suspended rule stays visible with its reason.
- project_policy.materialize_policy takes a findings_path (not event files) — its signature differs from
  the other projectors. Expected; plan test (b) accordingly.

TASKS:
1. Create tools/workflow/corpus_guard.py with guard_write(path, new_doc, extract): loads the existing
   file at path if present; extracts (watermark, primary_count) from old and new via the passed extractor;
   REFUSES the write (raise CorpusRegressionError with a message naming both watermarks and both counts)
   when the new watermark is OLDER than the existing OR the primary count is SMALLER — unless an authored
   override is active. Comparison rules: watermark comparison only when both docs have one; count
   comparison only when both have one; equal-or-newer + equal-or-larger passes untouched (diff-clean
   reruns must not be disturbed). Primary count per file: observation_count where present;
   experiment_candidates.json = source_findings; experiment_results.json = plan_count;
   capabilities.json = capability_count; associations.json = association_count.
2. Authored override (Clause 2): knowledge/corpus_regression_override.json —
   {"active": true, "reason": "<why>", "author": "<who>", "scope": ["findings.json", ...],
   "created": "<ISO>"}. RESOLUTION RULE: the guard locates both this file and policy_audit.ndjson
   relative to the DIRECTORY OF THE FILE BEING WRITTEN (path.parent), never a hardcoded knowledge/ path —
   tests run against temp knowledge dirs. When the guard permits a regression via override it APPENDS an
   audit record (file, old/new watermark, old/new count, reason, author) to policy_audit.ndjson in that
   same directory, then deactivates the override — but only after ALL files named in its scope have been
   written once in the current invocation (per-batch, not per-file, or multi-file projectors strand
   mid-run).
3. Wire guard_write into every per-output-file write in the six projectors (project_findings,
   project_associations [writes associations.json AND capabilities.json], project_coverage,
   project_capacity [4 files], project_experiments [2 files], project_policy). Each file gets its own
   extractor. Do NOT guard project_policy's existing policy_audit.ndjson append — that is an append-only
   audit stream, not a snapshot. known_good/bad_models.json have neither watermark nor count: do NOT
   invent one; leave them unguarded and record that in DECISION-NEEDED-A2.md (decision request, keep
   working) with your proposed extractor (e.g. len(entries)).
4. Tests (tests/workflow/test_corpus_guard.py): (a) normal advance passes; (b) THE INCIDENT — project the
   full fixture corpus to a temp knowledge dir, then re-project over a subset of event files: guard
   raises, file unchanged (for project_policy, simulate by projecting findings from fewer events first
   and feeding the smaller findings.json); (c) override path — same regression with override present:
   write succeeds, audit line appended, override deactivated after the batch; (d) diff-clean rerun
   (equal watermark, equal counts) passes untouched.

DEFINITION OF DONE: baseline + new tests green; the simulated-incident test proves the block;
BUILD-NOTES-A2.md lists the extractor per file.
OUT OF SCOPE: schema changes; changing WHAT projectors compute — only whether they may overwrite.
```

### B1 Payload Body (verbatim)

```text
STREAM B1 — MISSION: bring the authored documents in line with two adopted constitutional amendments and
with the fact that the derived store lost state in today's overwrite. Careful HTML editing in the existing
house style — no code. Preserve each file's CSS and structure; make no content changes beyond the six
edits below.

ASSUMPTIONS (verify in Phase 0):
- CAPABILITY-ROADMAP.html: "The Constitution" section (~line 110) with four div.law blocks; the Residue
  block (~line 131) titled "Residue — the one declaration that survives: what to observe."; the
  Enforceability (D18) block precedes it (~lines 124-129); Standing Guardrails table at ~lines 375-390
  (Rule/Why columns); S5 Shipped field ~line 190 and S5b Shipped field ~lines 217-225 both claim the
  build|ollama capability as corpus-proven; footer ~line 396 reads "association.v1 · capability.v1 (next)".
- CONSTITUTIONAL-REVIEW-2026-07-02.html: "Two amendments to the constitution" (~line 94), Amendment 1
  sentence at ~lines 103-105, Amendment 2 sentence at ~lines 116-119.
- TWO-ECONOMIES-WIND-TUNNEL.html: Δ1 stage card ~lines 173-183 (tag t-draft).
  ECONOMICS-ARCHITECTURE-REVIEW.html: "Δ1 superseded by this review" (~line 96).
- knowledge/capabilities.json: capability_count 0; knowledge/associations.json: association_count 0.

THE SIX EDITS:
1. CAPABILITY-ROADMAP.html — rewrite the Residue law block per Amendment 1: the residue is the CATEGORY
   "declarations of will" — what to observe AND what operating risk/budget is accepted — quoting the
   amendment sentence from the constitutional review verbatim. KEEP the original "Blind spots cannot
   discover themselves." sentence.
2. CAPABILITY-ROADMAP.html — add a new div.law block AFTER the Enforceability (D18) block, titled
   "Decision dimensions — the three obligations (Amendment 2)", body = the Amendment 2 sentence from the
   constitutional review, quoted verbatim.
3. CAPABILITY-ROADMAP.html — add one row to the Standing Guardrails table. Rule cell:
   "New scheduler decision dimensions carry the same three obligations as existing ones (explain /
   replay / test)". Why cell, verbatim: "A count-based smell test (&quot;no third _influence
   field&quot;) doesn't scale; the three obligations are the actual invariant (Amendment 2,
   CONSTITUTIONAL-REVIEW-2026-07-02.html)."
4. TWO-ECONOMIES-WIND-TUNNEL.html — inside the Δ1 stage card's header row, next to the existing DRAFT
   tag, add: <span class="tag t-authored">SUPERSEDED by Δ1′ — see
   ECONOMICS-ARCHITECTURE-REVIEW.html</span> (reusing the existing t-authored class is the chosen
   mechanism; do not add new CSS). Keep all original Δ1 content visible beneath — audit trail, not
   erasure.
5. CAPABILITY-ROADMAP.html — annotate BOTH the S5 Shipped field (~line 190) and the S5b Shipped field
   (~lines 217-225): append to each, inside the existing <p>, the sentence: " [Earned 2026-07-02 ·
   lost in the 2026-07-02 knowledge/ overwrite (see THREE-CHAIRS-PERSPECTIVES.html addendum) ·
   re-derivation pending.]" — exact wording as given here (it intentionally paraphrases the addendum;
   do not copy that file's phrasing). Do not delete the original claims.
6. CAPABILITY-ROADMAP.html — footer: change "→ association.v1 · capability.v1 (next)" to reflect shipped
   status: "· association.v1 · capability.v1 (shipped 2026-07-02)".

DEFINITION OF DONE: all SIX edits verifiable by grep (grep target per edit: the amendment-1 sentence; the
Amendment 2 block title; the new guardrail Rule text; "SUPERSEDED by Δ1′"; "re-derivation pending" — twice;
"(shipped 2026-07-02)"). Structural sanity check per edited file:
python -c "from html.parser import HTMLParser; HTMLParser().feed(open('<FILE>',encoding='utf-8').read())"
BUILD-NOTES-B1.md lists each edit with a before/after snippet. Baseline suite untouched and green.
OUT OF SCOPE: schema or code changes; THREE-CHAIRS-PERSPECTIVES.html and the review docs themselves.
```

### C1 Payload Body (verbatim)

```text
STREAM C1 — MISSION: contracts/capacity-observation.v1.schema.json (observed.physical) carries
model_residency as an enum state (null | cold_load | warm_resident | evicted_mid_run). The constitutional
review's Δ2 verdict says a warm/cold CLASSIFICATION is an authored threshold hiding inside telemetry:
producers should record RAW facts and the classification should be projected downstream. Fix the contract
now, before any producer populates the field — free now, expensive later.

ASSUMPTIONS (verify in Phase 0):
- Nothing populates observed.physical.model_residency: grep for model_residency across fixtures/, tools/,
  tests/ — expected hits are ONLY the schema itself, docs, and root HTML review docs. If any producer or
  fixture populates it, STOP and write QUESTIONS-C1.md.
- CONSTITUTIONAL-REVIEW-2026-07-02.html's Δ2 verdict (~lines 135-141) sketches raw-field names
  (model_loaded, last_model_load_event, last_model_unload_event). The field names in TASK 1 below are the
  chosen concrete refinement of that sketch and SUPERSEDE it — this divergence is expected and is NOT a
  contradiction.
- The model_residency property currently has NO description key (only type + enum).

TASKS:
1. In observed.physical, ADD nullable raw fields (additive; no required list touched;
   additionalProperties: false preserved): model_loaded_at_start (["boolean","null"] — was the target
   model already resident when the run began), model_load_count (["integer","null"] — loads during the
   run), model_unload_count (["integer","null"]), model_load_s (["number","null"] — total seconds spent
   loading). These are sensor-level facts a collector can report without any threshold.
2. KEEP model_residency (removal would be breaking) but ADD a description key stating: "DERIVED
   classification — computed by projection from the raw model_* fields; producers/collectors must never
   set this directly." Preserve its existing enum including the null member.
3. docs/physical-telemetry-instrumentation-findings.md — append one short dated note (at the end of the
   Status section) referencing the §4 priorities-table model_residency row and the Status field list:
   state that model_residency is now projection-derived and the four raw model_* fields are what
   collectors report.
4. Tests: NO test currently validates capacity-observation documents against the schema — CREATE
   tests/workflow/test_capacity_observation_schema.py following the house structural-check style (load
   the schema JSON; assert required ⊆ doc keys ⊆ properties; enum membership — see
   test_project_findings.py and test_project_associations.py for the pattern; jsonschema is NOT
   installed, do not import it). Cases: a doc with the raw model_* fields set and model_residency null
   is structurally valid; a doc with only raw facts is valid; the four new fields exist in the schema
   with the exact nullable types above.

DEFINITION OF DONE: schema change additive-only; suite green (baseline + your new test file);
BUILD-NOTES-C1.md shows the before/after of the observed.physical block.
OUT OF SCOPE: the projection that computes the residency classification (no evidence exists yet — the
constitution forbids scaffolding beliefs ahead of evidence); the collector (stream C2).
```

## Planned Intake Filenames

- `pour-b2.md`
- `pour-a1.md`
- `pour-a2.md`
- `pour-b1.md`
- `pour-c1.md`

Each file body will be:
- common preamble
- one verbatim stream body
- approved target-repo trailer with clone URL and `stream/<ID>` branch

## Open Questions For Derek

1. Approve or reject the mirror-first recommendation.
2. If approved, where should the conductor-side bare mirror of this repo live?
3. Do you want the smallest adapter set limited to repo/branch routing, with run export handled as a post-landing export step?
4. Is the existing untracked `PROVING-GROUND-ALPHA-PROPOSAL.html` expected to remain untracked while the pour proceeds?
5. Should the conductor’s existing dirty worktree be treated as untouchable unless a pilot stall forces a minimal fix?

## Checkpoint

Stop here. Do not dispatch until Derek approves this plan.

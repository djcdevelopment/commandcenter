Assay stream-scoped acceptance — gate laps on required-deliverable presence BEFORE ranking

CONTEXT. The fleet behavior assay repeatedly crowns "winners" that omit the stream's required
deliverables (four+ sightings; see docs/adr/0001-assay-acceptance-gap.md and
CONDUCTOR-FOLLOWUPS-2026-07-04.md section 2, plus ASSAY-ACCEPTANCE-GAP-2026-07-03.html). The
earlier proposed fix — a timeout / agent_rc grade cap — was RETRACTED and is WRONG: wave-2 proved
cc-builder-1 timed out yet produced the winning complete code every time. DO NOT implement any
timeout/agent_rc grade cap. The ONLY correct fix is STREAM-SCOPED ACCEPTANCE: before ranking laps
by behavior score, verify each lap produced the stream's REQUIRED deliverables and EXCLUDE any lap
that did not.

SCOPE / OWNERSHIP. This is conductor / assay-node code and is CONCURRENTLY OWNED by another agent.
Produce a BRANCH + tests ONLY. Do NOT merge, do NOT auto-promote — a frontier curate step lands it.
First LOCATE the ranking/grading entrypoint in your source tree (grep for the branch-comparison /
grade / finalize step, e.g. assay_compare_branches and its finalize path), READ it, and conform to
its existing structure, helpers, and test conventions. Change the minimum necessary.

CONVENTION TO IMPLEMENT (specified precisely — do not invent your own):
  - A stream DECLARES its required deliverables via a CCMETA field `requires`: a JSON list of path
    globs, e.g.  {"requires": ["tools/text/wordcount.py", "tests/workflow/test_wordcount.py"]}.
  - Absent or empty `requires` => the acceptance check is a NO-OP: every lap passes acceptance and
    ranking is byte-identical to today. This is the additive / back-compat guarantee for the many
    existing un-annotated streams.
  - For each candidate lap branch (ccfarm/<plan>/<worker>/lap1), enumerate its tree with
    `git ls-tree -r --name-only <branch>` and require that EVERY glob in `requires` matches at least
    one path (fnmatch). A lap missing ANY required deliverable is marked `acceptance_failed` and is
    EXCLUDED from the behavior ranking / tiebreak entirely (not down-weighted) BEFORE ranking runs.
  - Keep RECORDING agent_timed_out / agent_rc as observation METADATA (real signal for the belief /
    economics layers) — but they MUST NOT gate the winner. Only deliverable presence gates.
  - If ALL laps fail acceptance, surface that explicitly as a no-winner / needs-curation outcome
    rather than silently crowning the least-bad lap.

TESTS. Follow the assay module's existing test pattern; NO live SSH / network — mock the
`git ls-tree` shell-out exactly as the existing assay/preflight tests mock their shell-outs. Cover:
  - lap with all required deliverables present passes acceptance and ranks normally;
  - lap missing one required deliverable is excluded (acceptance_failed);
  - absent/empty `requires` => no-op, ranking byte-identical to today;
  - all-laps-fail => explicit no-winner signal;
  - the pour-c2 shape: one complete lap + one collector-only lap missing its required tests =>
    only the complete lap survives acceptance.

DELIVERABLE. Branch + passing tests + a note listing the EXACT function(s) and file path(s) you
changed, and confirmation that the empty-`requires` path is byte-identical to today's ranking.
DO NOT LAND — concurrently-owned code; landing is a separate frontier curate step.

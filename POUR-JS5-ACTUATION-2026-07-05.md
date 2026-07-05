JS5 — Scheduler-informed CCMETA ordering in submit_task (ADVISORY; flag default-OFF)

CONTEXT. This is the HEARTH job-shop scheduler's final unbuilt slice. JS1–JS4, JS6, JS7
are built and live; the CP-SAT shadow scheduler already exists under hearth/scheduler/ and
is exposed as the MCP tool propose_schedule (hearth/toolsurface/scheduler.py). JS5 wires
that shadow scheduler into dispatch BEHIND A DEFAULT-OFF ENV FLAG. This slice MUST NOT change
any dispatch behavior while the flag is off. Advisory-first, document-then-act (ADR-0008).

REPO / SCOPE. Branch off the current default branch of the commandcenter repo at your source
path. Touch ONLY:
  - hearth/toolsurface/task_lane.py
  - hearth/tests/toolsurface/test_task_lane.py
Do NOT modify conductor-side code, do NOT modify hearth/scheduler/, do NOT touch any other file.

WHAT TO BUILD.
In hearth/toolsurface/task_lane.py, submit_task():
  - Gate everything behind env flag HEARTH_SCHEDULER_ADVISE. Treat unset, "", "0", or any value
    other than exactly "1" as OFF.
  - Only engage the scheduler when the flag is ON AND the caller did NOT explicitly pass a
    `builders` argument (an explicit pin always wins — never override an explicit caller choice).
  - When engaged: import the scheduler LAZILY inside the flag-on branch (e.g.
    `from hearth.toolsurface.scheduler import propose_schedule`) so the flag-off path and any
    import failure never raise and never change behavior. Build a one-job snapshot for THIS task
    (a single job carrying its task_class / est_tokens) and call
    propose_schedule(jobs_snapshot=<that snapshot>). Use the returned proposal to ORDER / SELECT
    the candidate builders list. If the proposal names builders, adopt that order/subset as the
    chosen builders.
  - CRITICAL ORDER OF OPERATIONS: scheduler ordering happens BEFORE _ensure_fanout_minimum, and
    _ensure_fanout_minimum(chosen_builders) MUST still run AFTER it. Never regress the >=2
    fan-out phantom fix.
  - On ANY scheduler problem (flag on but import fails, solver raises, empty/invalid proposal):
    fall back SILENTLY to today's DEFAULT_BUILDERS behavior — but ledger that advise was attempted
    and fell back (through existing ledger machinery only; add no new event-writing path).
  - Doctrine: the conductor remains the ONE scheduler. HEARTH only ORDERS the eligibility list it
    already writes into the CCMETA header. Add no new dispatch path.

TESTS (hearth/tests/toolsurface/test_task_lane.py; unittest.TestCase; HEARTH_SCOPE temp-sandbox
pattern; mock ssh/dispatch EXACTLY as the existing test_task_lane.py does; NO live SSH / network /
gateway in any test):
  - flag OFF (unset): byte-identical to today — same inbox file written, same CCMETA builders,
    same return value. Assert this as an explicit regression guard.
  - flag ON + caller passed explicit builders: scheduler NOT consulted (explicit pin wins).
  - flag ON + no explicit builders + a MOCKED propose_schedule proposal: chosen builders follow
    the proposal order, and _ensure_fanout_minimum is still applied afterward.
  - flag ON + propose_schedule raises: falls back to DEFAULT_BUILDERS, no exception escapes,
    fallback is ledgered.

STANDING RULES. Additive only; legacy behavior/files keep working. The provider-contract test must
stay green. Run the FULL hearth suite before declaring done and REPORT THE COUNT. Do not touch the
existing repo test suite. Ledger only through existing machinery.

DELIVERABLE. Passing suite + a 5-line note: (a) how you built the single-job snapshot, and (b) how
flag-off byte-identity is guaranteed.

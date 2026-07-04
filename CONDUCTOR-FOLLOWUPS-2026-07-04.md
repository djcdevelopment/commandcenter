# Conductor Follow-ups — draft-ready, release on Derek's cue

Four items surfaced by the pour-c2 lap (the two instrument findings + the two open
dispatches). All are **conductor-side or fleet-dispatch** work: none is landable from
this repo, and per fleet-pacing they are staged here **ready to fire, not fired**.

The two patches target `scripts/conductor_maf.py` on the conductor
(`ssh claude@100.74.110.91`, repo `~/work/commandcenter`). Line anchors below are against
the local snapshot `.codex-temp/conductor_maf.py.remote` — **re-verify against the live
file before applying** (another agent commits there; the function bodies, not the line
numbers, are the contract).

---

## 1. Instrument finding #1 — CCMETA allow-list cannot opt in an excluded node

**Symptom (pour-c2):** `cc-builder-4` was requested via the CCMETA `builders` allow-list but
the daemon logged `requested builders missing from ready set: cc-builder-4` and proceeded
without it. Its ollama/mixtral debut silently did not happen.

**Root cause:** `load_nodes()` builds the ready set with
`"worker" in roles and not exclude_from_build_pool` (snapshot line ~161). `_select_builders()`
then only *intersects* the allow-list with that already-trimmed set (line ~95). An excluded
node is gone before the allow-list is ever consulted, so an allow-list can never re-admit it.
(Already documented in `docs/conductor-pour-howto.md` lines 59-65.)

**Fix — make the explicit allow-list override the exclusion flag.** Excluded nodes stay out of
the *default* pool (no allow-list → unchanged); an excluded node is re-admitted **only** when
it is explicitly named AND is a healthy real worker. Replace `_select_builders`:

```python
def _select_builders(builders, target_meta):
    allow=target_meta.get("builders") or []
    if not allow:
        return builders                       # default pool: exclusion still applies
    allowed=set(allow)
    picked=[b for b in builders if b[0] in allowed]
    present={b[0] for b in picked}
    # Allow-list OVERRIDES exclude_from_build_pool: re-admit any explicitly-named,
    # healthy worker trimmed ONLY by the exclusion flag (finding #1, pour-c2 2026-07-03).
    for name in sorted(allowed-present):
        ref=_node_extras(name)                # fleet.json entry (may be excluded)
        if ref.get("mcp_ready") and "worker" in ref.get("roles",[]):
            host=str(ref.get("tailnet",""))
            if _ssh_healthy(host, jump=ref.get("jump")):
                picked.append((name,host)); present.add(name)
            else:
                log.warning("allow-listed %s failed health probe - not re-admitted",name)
    missing=sorted(allowed-present)
    if missing:
        log.warning("requested builders missing from ready set: %s", ",".join(missing))
    return picked
```

Reuses existing `_node_extras` + `_ssh_healthy`. **Caveat to note in review:** this can re-admit
a node the operator explicitly named even if it is the assay node — acceptable (explicit intent),
but call it out. After landing, update the howto lines 59-65 (strike the "NO way to opt an excluded
node into a single run" paragraph; the allow-list now does exactly that).

**Test to add** (conductor test suite): allow-list naming an `exclude_from_build_pool: true`
healthy worker → present in returned builders; same node with NO allow-list → absent.

---

## 2. Instrument finding #2 — assay grade not capped on a timed-out / nonzero-rc lap

**Symptom (pour-c2):** cc-builder-1 timed out (`agent_rc -1`, collector only, zero tests) and
cc-builder-2 shipped all four deliverables (171/171). The behavior assay graded **both B/70** —
it weighs neither the timeout flag nor deliverable presence. The debiased tiebreak then detected
position bias, declared no consensus, and fell back to list order — crowning the timed-out lap.

**Root cause:** the behavior assay (`assay_compare_branches`, runs on the assay node) grades branch
artifacts and is blind to the agent's exit signal. `build_one` DOES capture `agent_rc` /
`agent_timed_out` (snapshot line ~230) but `finalize` never uses them to gate the score.

**⚠ RETRACTED (2026-07-04) — the naive timeout grade-cap is WRONG. Do NOT apply it.**
The earlier proposal ("cap any `agent_timed_out`/nonzero-rc lap to 0 before the tiebreak") was
falsified by the wave-2 evidence gathered the same day. In all five wave-2 pours (d1/e1/f1/ga/gc),
**cc-builder-1 shows `agent_timed_out=True, agent_rc=-1` yet produced the winning, complete,
landable code every time** (d1/e1 landed to master green untouched; f1/ga's code landed with
curated tests). The harness timeout there fired *after* a clean commit — it does not indicate an
incomplete lap. Capping on it would have demoted the builder that actually delivered and handed
the win to a lesser lap. `agent_timed_out` is NOT a reliable proxy for deliverable quality:
- pour-c2: timeout coincided with an INCOMPLETE lap (collector only, missing tests) → should lose.
- wave-2: timeout coincided with COMPLETE laps (all deliverables, passing tests) → should win.
Both scored B/70 with passing baseline tests. No timeout/rc/test-count signal separates them.

**The only correct fix is stream-scoped acceptance (#2b) — and it lives in the assay layer, not
`conductor_maf.py`.** The ranking assay (`assay_compare_branches`, on the worker/assay node) must
check whether each lap produced the stream's REQUIRED deliverables, and gate laps that didn't,
BEFORE ranking by behavior score. That correctly handles both cases: pour-c2's incomplete lap
fails acceptance (excluded); wave-2's complete laps pass and rank normally regardless of the
timeout flag. This needs:
1. A per-stream way to DECLARE required deliverables — e.g. a CCMETA `requires: [<paths/globs>]`
   field, or a `## DOD:` manifest block the builder prompt already implies. (The DoD lines in
   FLEET-WORK-PLAN.html streams are exactly this list, currently unstructured.)
2. `assay_compare_branches` to verify presence (git ls-tree on each `ccfarm/<plan>/<worker>/lap1`
   branch) + optionally run stream-declared checks, and mark a lap `acceptance_failed` (score 0 /
   excluded from the tie) when a required deliverable is absent.
3. The conductor already surfaces `agent_timed_out` in the observation — keep recording it as
   METADATA (it's real signal for the belief/economics layers), just don't let it gate the winner.

Design context in `ASSAY-ACCEPTANCE-GAP-2026-07-03.html`. This is its own stream (assay-node
change + a stream-manifest convention), not a `conductor_maf.py` patch. **Do not ship the timeout
cap as an interim measure — wave-2 proved it does active harm.**

Evidence for this retraction is preserved in `runs/regression-probe-ccb1/PROVENANCE.md` (why the
wave-2 build observations were withheld from the belief layer for the same reason).

---

## 3. Regression_probe retest — clear the cc-builder-1 quarantine (READY TO FILE)

`policy.json` carries a **quarantine** on `cc-builder-1|sonnet|claude` (pour-b2 success →
pour-c2 timeout) and the belief layer already emitted a `regression_probe` candidate demanding
the retest. One clean lap that includes cc-builder-1 re-projects the regression back toward
known_good/uncertain and lifts the quarantine.

**To fire:** drop this file in the conductor's `inbox/` as `regression-probe-ccb1.md`
(stem = plan_id). The conductor needs ≥2 fan-out targets, so pair it with cc-builder-2. Keep the
task small and deterministic so the signal is clean (this is a re-assay, not feature work):

```
<!-- CCMETA
{"builders": ["cc-builder-1", "cc-builder-2"], "promote": false}
-->
Regression probe for cc-builder-1 (sonnet). Small, self-contained, deterministic task.

Add `tools/text/wordcount.py` exposing `word_count(text: str) -> dict` returning
{"words": int, "lines": int, "chars": int} (words split on any whitespace, lines split on
"\n", chars = len(text)); empty string returns all zeros. Add
`tests/workflow/test_wordcount.py` covering: empty string, single word, multiple words with
mixed whitespace, multi-line text, and trailing-newline handling. Run
`python -m unittest discover -s tests/workflow` and report the count. Do not touch any other file.
```

`promote:false` — this is a probe, not a landing; the point is the graded observation, not the code.
After it grades: pull the run's `result.json` into `runs/<plan-id>/conductor/`, materialize the
observation, and re-run the projection chain to update the finding + lift the quarantine.

---

## 4. Capability re-earn + wave-2 remainder

**Capability re-earn (G3, blocked on infra).** `capabilities.json` = 0; G3 wants ≥1. It needs a
real **build workflow** observation for `omen-worker-1` (a second `build|ollama` data point), which
must be graded by the assay node — **claudefarm1**, currently down (was taken for cc-builder-4
provisioning). Blocked until claudefarm1 (or another `assay`-role node) is healthy. When it returns,
dispatch one normal build lap allow-listing `omen-worker-1`. *Note:* today's G2 lap produced a real
`omen-worker-1` observation but it is `physical-telemetry-validation`, NOT a build — it deliberately
does **not** re-earn the build capability.

**Wave-2 remainder — D1, E1, F1, Ga, Gc (dispatchable on cue).** All go through the now-proven
campaign path (`docs/conductor-pour-howto.md`): file each as an `inbox/*.md` work item, let the
fleet build/assay, curate if the assay mis-selects (the grade-cap in #2 should reduce that), land
from OMEN. Stream specs live in `FLEET-WORK-PLAN.html`. Held draft-ready per fleet-pacing — release
on Derek's cue, ideally after patch #2 lands so wave-2 laps get the timeout/acceptance guard.

---

### Status — updated 2026-07-04 (post wave-2 curation)
- **Item 1 (allow-list overrides exclusion): APPLIED + LIVE on the conductor** (`63935ee`, service
  restarted, clean). cc-builder-4's mixtral debut can now be opted in per-run.
- **Item 2 (assay grade-cap): RETRACTED — do not apply.** Wave-2 proved the timeout cap does active
  harm. The correct fix is stream-scoped acceptance in the assay layer (see the retraction above).
- **Item 3 (regression retest): DONE.** Filed, ran clean, and the quarantine was LIFTED via
  re-projection (`master` @ `f256d38`, this repo). cc-builder-2 upgraded to known_good as a bonus.
- **Item 4:** wave-2 D1/E1/F1/Ga/Gc were dispatched, curated, and LANDED to master (suite 177→264);
  belief-layer fold-in of their build observations deliberately withheld (poisoning risk — see
  `runs/regression-probe-ccb1/PROVENANCE.md`). Capability re-earn (G3) still blocked on claudefarm1.
- **G2: OPEN** (`runs/g2-validation/`).
- **claudefarm1:** diagnosed — golden VM renamed to Hyper-V `cc-worker-1`, powered on but
  unreachable on the current NAT gen (see `fleet/inventory.toml`). Recovery is a VMConnect/guest
  fix. Re-homing `omen-worker-1`'s shell off claudefarm1 remains a worthwhile resilience follow-up.

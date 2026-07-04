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

**Fix (immediate, mechanical) — cap a timed-out/nonzero-rc lap's score before the tiebreak** so a
false tie can't be resolved by list order. In `finalize` (line ~417), right after the assay call:

```python
        if assay:
            assay_res=await run_assay(assay[0],assay[1],plan_id,[b[0] for b in builders],
                                      repo_path=target_meta.get("repo_path"))
            _cap_incomplete_scores(assay_res, results)      # <-- finding #2, pour-c2
            winner=assay_res.get("winner")                  # re-read: cap may have moved the top
            tb=await _tiebreak(plan_id, plan_text, assay_res, repo_path=target_meta.get("repo_path"))
            ...
```

New helper (near `_tied_top`):

```python
def _cap_incomplete_scores(assay_res, build_results):
    """A lap that timed out or exited nonzero cannot outrank a clean lap. The behavior
    assay is blind to the agent exit signal; without this a bare, timed-out lap ties a
    complete one at B/70 and the debiased tiebreak falls to list order (pour-c2 2026-07-03)."""
    sb=assay_res.get("scoreboard") or []
    for row in sb:
        e=build_results.get(row.get("worker")) or {}
        if e.get("agent_timed_out") or (e.get("agent_rc") not in (0, None)):
            row["score"]=0
            row.setdefault("flags",[]).append("capped:agent_incomplete")
    sb.sort(key=lambda r:(r.get("score") or 0), reverse=True)
    assay_res["scoreboard"]=sb
    top=sb[0] if sb else None
    assay_res["winner"]=top.get("worker") if (top and (top.get("score") or 0)>0) else None
    # winner=None on an all-capped board -> the existing _promote health-gate refuses (correct).
```

This composes with the existing promote health-gate (empty/all-zero scoreboard → promote refused,
snapshot line ~485) and `_tied_top`'s positive-score requirement.

**Fix (deeper, follow-on) — stream-scoped acceptance checks (#2b).** The grade-cap stops the
timeout exploit but does NOT catch a lap that runs to completion yet omits required deliverables
(cc-builder-2's collector was complete-but-wrong). The design for a per-stream acceptance assay
(required-file presence + stream-declared checks, run as an acceptance gate *before* the ranking
assay) is in `ASSAY-ACCEPTANCE-GAP-2026-07-03.html`. That is a larger change and its own stream —
track it separately; the grade-cap above is the immediate guard.

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

### Status at time of writing (2026-07-04)
- Suite 177/177 green; master @ `13ba046`; belief store: 18 findings (1 regression, 1 quarantine),
  1 block, 3 exploratory_only.
- **G2: now OPEN** (this session — see `POUR-STATUS.md` "Landing: G2" + `runs/g2-validation/`).
- Items 1-2 = code patches above (conductor). Item 3 = ready-to-file inbox item. Item 4 = blocked
  (claudefarm1) / on-cue (wave-2).

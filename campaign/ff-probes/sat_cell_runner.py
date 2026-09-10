#!/usr/bin/env python3
r"""SAT-L1 cell runner -- the saturation surface, one pre-registered cell at a time.

Spec: ``docs/prereg/SATURATION-SURFACE-LAP1.md`` (tag ``prereg-saturation-lap1-20260909``).
This runner does NOT re-implement that card's measurement protocol. It COMPOSES the tools
that already implement it and adds only the pieces nothing owns yet.

WHAT IT COMPOSES (unchanged behaviour, called not copied)
  ``ff_cell.py``        the per-cell invariant: epoch -> (Phase 2) restart + real ready
                        marker -> pre-rate -> b70tools sample INSIDE the pre-rate activity
                        window (finding A12) -> health/placement gate -> ONE cell ->
                        post-rate -> the row carrying the nine provenance fields. The cell
                        it runs is the qwen38 ``load`` invocation, passed as ``--command``.
                        The runner calls it with ``--no-ledger --json-out`` so exactly ONE
                        row per cell reaches the ledger: this runner's SAT-L1 receipt, with
                        ff_cell's nine fields folded in.
  ``ff_ratecheck.py``   pre/post rate, the 0.80/0.90 envelope, ``--set-baseline`` after a
                        Phase 2 restart. Unmodified.
  ``qwen38_campaign``   the load generator (``load``) and ``summarize_rows`` -- jobs/hour on
                        the busiest-client wall and nearest-rank percentiles, imported and
                        called, never re-derived.
  ``hearth.health.guard``  the ported production guard (stop on degraded/stalled/
                        unreachable; a sample predating the cell is never a pass).
  ``hearth.rotation.telemetry``  ``b70_snapshot`` / ``commit_free_gb`` for admission.
  ``hearth.rotation.preflight``  run before every Phase 2 window; NO-GO stops.
  ``sat_reference_capture``  its ``reduce_stream`` (watts = dJ/dt per card), ``symmetry``
                        (corpus/verdict.py) and its bearer/rung patterns.
  ``corpus/verdict.py`` the symmetry gate; ratio < 0.5 -> the row is ``partially_scored``.
  ``etw6_watch`` + ``etw10_package`` + ``etw4_depth``  depth-0, per the card's revised
                        protocol. The runner NEVER starts or stops an ETW session; with no
                        ``session-manifest.json`` the depth-0 fields are ``null``, recorded.

WHAT IT ADDS (the only new logic)
  1. Warm-to-flatness (ADR-0043): ``ff_ratecheck.measure(rung, 3)`` until
     ``repeat_spread_pct <= 2.0`` (capped, every iteration recorded -- the FIRST iteration's
     FIRST rep is the "unwarmed rep-1" P7 needs), then ONE DISCARDED depth-matched ``load``
     at N=2 so a deep block warms its own KV paths. Never a single discarded request.
  2. In-flight capture: ``/slots`` polled at 1 s with the bearer -> slot-busy fraction;
     ``/metrics`` scraped before and after -> ``llamacpp:n_busy_slots_per_decode`` delta.
     EVERY poll is kept, and the gate-4 figures are computed over the LOAD window
     (``min(started_at)``..``max(completed_at)`` on the load's own rows) because the
     bracket also contains ff_cell's single-stream pre- and post-rate probes -- the span
     fractions are span-diluted and are recorded beside the window ones, never instead.
  3. Admission: budget headroom per card from the b70tools stream; negative -> the row is
     flagged ``over_admitted`` and KEPT (excluded from the surface, never dropped).
  4. Phase 2 ``-np``: token-exact edit of the PRODUCTION entry in
     ``fleet/arcserve/llama-swap/omen.yaml`` -> ``schtasks /Run /TN ArcServeRestart``, which
     is STOP-ONLY -> poll until nothing listens on 8081/8082 -> ``schtasks /Run /TN
     ArcServeBoot`` -> the REAL ready marker -> ``/health`` 200 AND a real completion ->
     ``ff_ratecheck --set-baseline`` whose ``--note`` names ``-np`` and ``-ub`` -> a row
     appended to ``epoch_boundaries`` -> warm. ``-np 2`` restored at the end. Refused
     outright while ``hearth\var\arc-maintenance.stop`` exists, and aborted BEFORE the boot
     if that shared lock appears while production is stopping.
  5. Board duty cycle against the FROZEN reference receipt (read from the receipt, never
     hardcoded): the fraction of CELL WALL TIME each card's dJ/dt is above 0.9x ITS OWN
     reference burst p50, per card, keyed by PCI BDF. ⚠ That reference is a ~92-second
     prefill burst standing in for workloads that run hours, so every duty fraction ships
     with ``reference_caveat`` and ``reference_burst_window`` -- see ``DUTY_REFERENCE_CAVEAT``.
  6. Prefill that is real (gate 7): the load runs with ``--no-cache-prompt``, and the
     server's own ``llamacpp:prompt_tokens_total`` delta is checked against the prompt
     tokens the cell's rows report. The surface's size axis (512 / 8K / 32K) IS prefill, so
     a cached prefix is a refused cell -- ``status_reason`` marked and the row KEPT, like
     ``over_admitted`` -- rather than a quietly nulled rate.
  7. Per-round launch skew (INSTRUMENT, not a gate -- the prereg's 2026-09-09 protocol
     addition). After the load, the tail of ``hearth\var\arc-serve.log`` is read and the
     server's own ``new prompt`` lines for THIS cell's rounds are timed against each other.
     The noise-floor cell's jobs/hour is bimodal because one round per affected cell has its
     two concurrent requests launched ~222 ms apart instead of ~0.2 ms; recording the skew
     per round means a point on the surface SAYS whether it hit the event, rather than
     having it averaged into a variance. NO gate reads ``launch_skew`` -- not gate 8, which
     is the thermal gate and reads only the temperature counters -- and a delayed round
     never changes a cell's status. A missing log or an anchor that cannot be established
     is a LOUD NULL (``{"rounds": null, "reason": ...}``), never an exception and never a
     silent zero.
  8. Thermal (gate 8, added 2026-09-09). b70tools already streams ``gpu.temperature_c`` and
     ``vram.temperature_c`` into every cell's ``b70/events.jsonl`` and nothing read them.
     ``thermal_summary`` reduces them per card and per counter to an idle baseline, busy
     p50/p95, max and delta; ``score_thermal`` scores that against the module constants.
     BOTH the absolute and the delta are reported, because absolute temperature is dominated
     by AMBIENT: measured across four cells in one session the idle VRAM baseline swung
     56-62 C while the workload-attributable rise was only 0-4 C. An absolute-only gate is
     seasonally optimistic; a delta-only gate misses a genuinely hot room. A ``fail`` KEEPS
     the row and excludes it from the surface, exactly like ``over_admitted``.

TIMEBASE. b70tools' ``t`` on ``ms`` rows is NANOSECONDS on the boot-relative perf_counter
clock -- never QPC ticks; reading it as 10 MHz ticks is wrong by 100x and once turned a
1-second snapshot into a "105-second capture" (sat_reference_capture.py, measured
2026-09-09). The cell window is mapped into that clock by taking ``time.time()`` and
``time.perf_counter_ns()`` together at stream launch, in this process, and converting the
harness rows' wall stamps through that pair.

BEARER. ``OMEN_ARC_TOKEN`` comes from the launcher environment; run this through
``hearth\etc\with-gateway-env.cmd`` from PowerShell:

    & cmd /c "C:\work\commandcenter\hearth\etc\with-gateway-env.cmd fleet-worker-node\.venv-omen\Scripts\python.exe campaign\ff-probes\sat_cell_runner.py --one-cell np2-p512-c2-r1 --live"

This module NEVER reads ``hearth\var\gateway.cmd``; it reads the environment variable only,
passes it to the harness as ``QWEN38_API_KEY`` and to the pollers as ``Authorization:
Bearer``, and records only the variable's NAME and LENGTH. (``ff_ratecheck._token`` --
existing, reused unmodified -- does read that fragment; that is its own contract, stated
here so the boundary is explicit rather than implied.)

SAFETY. ``--dry-run`` is the DEFAULT: it prints the exact command chain and the gate
decisions and performs no network, GPU or filesystem-mutating action. ``--live`` is the
explicit opt-in, ``--one-cell`` runs exactly one cell, and the full sweep needs ``--sweep``
as well. The operator runs live cells.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import io
import json
import math
import os
import re
import shlex
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "campaign" / "lz-probes"))

import ff_cell            # noqa: E402  the invariant harness; composed, never re-implemented
import ff_ratecheck       # noqa: E402
import sat_reference_capture as satref  # noqa: E402  reduce_stream / symmetry / timebase

PROBE = "SAT-L1"
LEDGER = Path(ff_cell.LEDGER)

#: MACHINE STATE lives in the live checkout, not in whatever tree this file was run from.
#: ArcServe reads ``C:\work\commandcenter\fleet\arcserve\llama-swap\omen.yaml``; the
#: maintenance sentinel is a SHARED lock at an absolute path (hearth/imagegen/session.py);
#: ``rungstate`` reads ``hearth\var\arc-keepalive.jsonl`` there. Editing a worktree copy of
#: any of them would be a silent no-op against production -- exactly the failure class this
#: lab exists to catch -- so they are pinned here and the dry run says so when the runner is
#: executing from somewhere else.
LIVE_ROOT = Path(os.environ.get("SAT_L1_LIVE_ROOT") or r"C:\work\commandcenter")
OMEN_YAML = LIVE_ROOT / "fleet" / "arcserve" / "llama-swap" / "omen.yaml"
BASELINES = Path(ff_ratecheck.BASELINES)
MAINTENANCE_STOP = LIVE_ROOT / "hearth" / "var" / "arc-maintenance.stop"
#: llama-server's own log, at ``-lv 5``. READ-ONLY here, and only its tail: see
#: ``parse_serve_log_launches``. This is production's log in the live checkout, never a
#: worktree copy, for the same reason omen.yaml is pinned above.
SERVE_LOG = LIVE_ROOT / "hearth" / "var" / "arc-serve.log"
QWEN38 = REPO / "campaign" / "qwen38" / "qwen38_campaign.py"
VERDICT_PY = REPO / "corpus" / "verdict.py"
ETW10 = REPO / "campaign" / "lz-probes" / "etw10_package.py"
ETW4 = REPO / "campaign" / "lz-probes" / "etw4_depth.py"
ETW_RECORDER = Path(r"E:\work\battlemage\ff-probes\etw-recorder")
ETW_SESSION_MANIFEST = ETW_RECORDER / "session-manifest.json"
ETW_RING = ETW_RECORDER / "lz_dxgk_ring.etl"
REFERENCE_RECEIPT = Path(r"E:\work\battlemage\sat-l1\reference\ref-20260909T085437Z\receipt.json")
CELLS_ROOT = Path(r"E:\work\battlemage\sat-l1\cells")
B70TOOLS = satref.B70TOOLS
NS = satref.NS

PRODUCTION_MODEL_KEY = "qwen3-30b-a3b"
PRODUCTION_PORT = 8082
BEARER_ENV = "OMEN_ARC_TOKEN"
HARNESS_KEY_ENV = "QWEN38_API_KEY"
MODEL_ALIAS = "qwen3-30b-a3b"
TOPOLOGY = "dual-split"
CANDIDATE = "production"
SEED = 38027
MAX_TOKENS = 200
REQUESTS_PER_CLIENT = 3
TOTAL_CTX = 131072          # -c 131072, fixed; per-slot context is TOTAL_CTX // np
BASE_NP = 8                 # production's standing value, restored by --restore-np.
#: 2 -> 8 on 2026-09-09 (SAT-L1 Lap 1B): 3,358 jobs/h vs 2,128 at -np 2 (x1.58) for
#: Qwen3-30B-A3B at 512-token prompts, dual layer-split. -np 16 REGRESSES ~32%, so 8 is
#: the peak for THIS model at THIS depth -- a regime, not a universal setting.
#: LOAD-BEARING FOR --restore-np: leaving it at 2 would silently revert the operating
#: point at the end of a sweep and re-break the backends.toml lockstep (context_bytes
#: 57344 / parallel_slots 8 track -c/-np; a slot holds 16384 tokens now).

CARD_NP = 2                 # the -np the Lap 1 card's predictions were REGISTERED at.
#: Split out from BASE_NP on 2026-09-09, when production moved to 8 and the two meanings
#: stopped coinciding. This one is HISTORY and must not track production: the noise-floor
#: cell (N=2 / 512) and P5 were pre-registered, run and scored on the -np 2 surface, and
#: changing what "phase 1" or "the noise floor" refers to after the fact would silently
#: re-point a tagged pre-registration at cells it never covered.
UB = 1024                   # production's -ub, named in every re-baseline note

#: ADR-0043 warm gate. Three consecutive reps within +-2% (the card's gate 1).
FLATNESS_SPREAD_PCT = 2.0
FLATNESS_REPS = 3
FLATNESS_MAX_ITERATIONS = 6

#: Duty cycle: above 0.9x THIS card's own frozen reference burst p50 (card, gate 5).
DUTY_THRESHOLD_FRAC = 0.9

#: ⚠ THE DENOMINATOR IS A TRANSIENT, AND EVERY DUTY NUMBER IN THIS CAMPAIGN INHERITS IT.
#:
#: The frozen reference is a ~92-second prefill burst at 2 clients (``ref-20260909T085437Z``,
#: frozen ``7d06fb0``). The workloads it stands in for run for hours -- the longest real one
#: measured on this box is a 3.59-hour imagegen session, 850 images at a pool duty of 1.90 of
#: 2, roughly 140x the reference window. Derek's field observation of that lane is 90 C within
#: thirty minutes and three to four hours of bouncing off it.
#:
#: So a duty number answers "was this card above 0.9x what a 92-second prefill burst drew",
#: NOT "was this card working as hard as it does under a real long workload". Two things
#: follow, and neither is hypothetical:
#:   * The reference is not a ceiling. A real serving cell has already exceeded it -- 181.2 W
#:     peak at ``-np 8`` with 16 clients against a 159.92 W reference p50 (claim register #28).
#:   * It is not a thermal steady state either -- but NOT because the capture is missing.
#:     ⚠ CORRECTED 2026-09-09. This bullet used to read "NO sustained-load capture exists on
#:     this box: the longest in the corpus is ~260 ticks, and every burn-in file carries
#:     exactly one temperature sample per adapter." Both halves were a SCOPE error, not a
#:     typo. "~260 ticks" was the longest in sat-l1 + ff-probes only (296, in
#:     ff-probes\statewatch-20260830); and the "one sample per adapter" reading came from the
#:     14 one-shot snapshot dirs named ``b70tools-<stage>-<timestamp>`` under
#:     E:\work\battlemage\burnin-2026-08\results -- while SIX sustained runs sat in that same
#:     folder under the opposite naming convention, ``<name>-b70tools``. A glob on
#:     ``b70tools-*`` matches all 14 snapshots and ZERO sustained runs. State the scope of a
#:     search before generalising its emptiness to "this box".
#:     What is actually on disk: ``soak1-b70tools`` carries 6.22 h of B70 telemetry (then
#:     day2 257.6 min, finale 61.2 min, idle-baseline 10.2 min). soak1 shows precisely the
#:     trajectory a burst p50 cannot: 0000:04:00.0 VRAM peaks 94 C at ~2.1-2.6 h -- ONE degree
#:     under VRAM_ABORT_C -- then settles 82-84, running 4-6 C over 0000:09:00.0 at p50. This
#:     was already written up as findings F7/F8 of OMEN-LIMIT-TEST-2026-08.html in August; it
#:     was measured and never read back here.
#:     The bound SURVIVES the correction and sharpens. Reduced via
#:     ``sat_reference_capture.py --reduce ... --counter gpu``, soak1's sustained per-interval
#:     p50 is 160.48 W (bus 9) / 145.15 W (bus 4) -- ABOVE this burst reference's own
#:     143.93 / 102.64 by 11.5% and 41.4% -- at a duty of 0.602 / 0.603 across the 6.22 h.
#:     So the denominator is not merely SHORT, it is LOW, and most severely on bus 4.
#:
#:     A PURPOSE-BUILT sustained reference landed the same day: 98.5 min, passive,
#:     ``cap-20260909T173248Z`` (claim register #33), which found the first thermal PLATEAU
#:     measured here -- 72 C within 9 min at 112 W tile power on the loaded card. Note the two
#:     captures say different things and #33 does not supersede this one: #33 is a MIXED
#:     workload well BELOW burst power, so it is frozen BESIDE this reference; soak1 runs
#:     ABOVE it. #33 is the first sustained reference deliberately frozen, NOT the first
#:     sustained capture -- soak1 predates it by three weeks and is 3.8x longer.
#:
#: This rides on every receipt (``duty_cycle()["reference_caveat"]``) so a consumer cannot read
#: the fraction without the denominator. Its THERMAL half is now retired -- by #33 deliberately,
#: and it was retirable FROM DISK all along: reduce soak1 with ``--reduce ... --counter gpu``,
#: which touches no GPU. What still needs a NEW capture is BOARD power:
#: ``card.energy_j_counter`` is emitted once per capture, so watts at the board can never be
#: differenced out of any file already written. That is a b70tools change, not a longer run.
#: Neither half is ever retired by rewording.
DUTY_REFERENCE_CAVEAT = (
    "the reference is a ~92 s prefill burst at 2 clients; the workloads it stands in for run "
    "for hours (longest measured: a 3.59 h imagegen session at pool duty 1.90/2, ~140x this "
    "window). It is not a power ceiling -- a real cell peaked 181.2 W over a 159.92 W reference "
    "p50. Duty means 'above 0.9x a 92 s burst', not 'as loaded as a real long workload'. "
    "UPDATED 2026-09-09: sustained captures EXIST. The purpose-built one is 98.5 min "
    "(cap-20260909T173248Z, claim register #33); it found the first thermal plateau measured "
    "here -- 72 C within 9 min at 112 W tile power on one card -- and being a MIXED workload "
    "well below burst power it is frozen BESIDE this reference, not over it. CORRECTED the same "
    "day: 'no sustained capture exists' was a SCOPE error and was never true -- soak1-b70tools "
    "under burnin-2026-08 is 6.22 h from 2026-08-20, peaks 94 C on VRAM at hour two, and its "
    "sustained p50 (160.48 / 145.15 W) runs ABOVE this burst p50 on BOTH cards, so the "
    "denominator is LOW as well as short. Board power stays unobtainable at any length: "
    "card.energy_j_counter is emitted once per capture."
)

# ---------------------------------------------------------------- gate 8: thermal --
#: THE ABORT LIMIT IS DEREK'S CALL, MADE 2026-09-09: 95 C. In his words -- "if we hit that,
#: we back off. i've cooked these cards plenty of time, they'll be fine."
#:
#: That is a TENANCY decision about his own hardware, not a datasheet number and not a
#: margin someone else gets to shave. DO NOT lower it as a "safety improvement" without
#: asking him: a tighter limit would abort cells he has decided are fine to run, and the
#: surface would quietly lose points to a threshold nobody chose.
#:
#: What it answers is a real precedent, not a hypothetical: the replica-per-card experiment
#: was quarantined at 96 C on the VRAM of 0000:04:00.0 at only p512-c4
#: (E:\work\battlemage\qwen38-bench-2026-08\results\quarantine\performance-cells.jsonl;
#: docs/adr/0038).
VRAM_ABORT_C = 95.0
VRAM_WARN_C = 88.0
GPU_ABORT_C = 95.0
GPU_WARN_C = 88.0

#: The WORKLOAD-ATTRIBUTABLE rise (busy p95 - idle), not an absolute, and it warns rather
#: than aborts. Absolute temperature here is dominated by AMBIENT: that 96 C abort was during
#: a hot week in late summer and it is now approaching fall. Measured across four cells in
#: one session the idle VRAM baseline swung 56-62 C (6 C of room) while the rise under load
#: was only 0-4 C. So an absolute-only gate is seasonally optimistic and a delta-only gate
#: misses a genuinely hot room -- BOTH are reported, and either can raise a warn.
DELTA_WARN_C = 15.0

#: The two counters b70tools already writes into every cell's stream.
#: The cooldown line the 2026-08-27 harness enforced (``temperature_resume_below_c: 80``) and this
#: runner does NOT. It is printed when a cell aborts thermally so the operator has the number, but
#: nothing here blocks a re-run: resuming is a decision about Derek's own hardware, not the
#: runner's to make. ``thermal_watchdog.RESUME_BELOW_C`` is the same value for the same reason.
THERMAL_RESUME_BELOW_C = 80.0

THERMAL_COUNTERS = ("gpu.temperature_c", "vram.temperature_c")

#: idle = the coolest reading in the capture's LEADING QUARTER, before the load window.
THERMAL_IDLE_FRACTION = 0.25

#: Short names for the gate's own prose. The counters keep their b70tools names everywhere
#: else, so a receipt field is always searchable by the name the stream uses.
THERMAL_LABELS = {"gpu.temperature_c": "GPU", "vram.temperature_c": "VRAM"}

THERMAL_ATTRIBUTION = ("abort %.0f C is Derek's call, 2026-09-09 -- a tenancy decision about "
                       "his own cards, not a datasheet limit; do not tighten it without "
                       "asking him" % VRAM_ABORT_C)
THERMAL_AMBIENT_NOTE = ("absolute readings track AMBIENT (idle VRAM swung 56-62 C across four "
                        "cells in one session while the load added only 0-4 C), so the delta "
                        "is reported beside the absolute and the 96 C quarantine precedent "
                        "was a late-summer reading, not a fixed property of the board")


def thermal_thresholds() -> dict:
    """The gate's numbers as data, so a receipt carries what it was scored against."""
    return {
        "gpu.temperature_c": {"warn_c": GPU_WARN_C, "abort_c": GPU_ABORT_C},
        "vram.temperature_c": {"warn_c": VRAM_WARN_C, "abort_c": VRAM_ABORT_C},
        "delta_warn_c": DELTA_WARN_C,
        "attribution": THERMAL_ATTRIBUTION,
        "ambient": THERMAL_AMBIENT_NOTE,
    }


#: corpus/verdict.py's own warn level. Below it the row is partially_scored, not dropped.
SYMMETRY_PARTIAL_BELOW = 0.5

DEPTHS = (512, 8192, 32768)
PHASE1_N = (1, 2, 4, 8, 16, 24)
PHASE2_N = (2, 4, 8, 16, 24)
PHASE2_NP = (4, 8)
DEPTH0_N = (2, 16)          # the card: one depth-0 capture per depth block at N=2 and N=16

CELL_RE = re.compile(r"^np(?P<np>\d+)-p(?P<depth>\d+)-c(?P<n>\d+)-r(?P<rep>\d+)$")

GATES = (
    (1, "warm_then_measure"),
    (2, "production_guard"),
    (3, "admission"),
    (4, "in_flight"),
    (5, "board_duty_cycle"),
    (6, "depth0_fraction"),
    (7, "prefill_real"),
    (8, "thermal"),
)

#: Every field a SAT-L1 receipt carries. Present or ``null`` -- never absent, never dropped.
RECEIPT_FIELDS = (
    # --- identity -----------------------------------------------------------------
    "schema_version", "ts", "probe", "cell", "coresident", "phase", "status", "status_reason",
    "runner_commit", "receipt_path",
    # --- regime (every number carries it) -------------------------------------------
    "regime",
    # --- the nine provenance fields (ADR-0041/0042), from ff_cell's own row -----------
    "incumbent_process_epoch", "incumbent_restarted_since_cotenancy",
    "incumbent_rate_fraction_pre", "incumbent_rate_fraction_post",
    "placement_assertion", "placement_evidence", "health_gate_passed",
    "receipt_status", "receipt_status_reason",
    # --- gate 1: warm-to-flatness (ADR-0043) ------------------------------------------
    "warm", "unwarmed_rep1_tok_s", "warm_discard_load",
    # --- gate 2: the production guard --------------------------------------------------
    "guard_before", "guard_after", "guard_verdict",
    # --- the load itself ----------------------------------------------------------------
    "load_command", "load_rows_path", "load_requests", "summary", "cache_prompt",
    "jobs_per_hour", "latency_p50_s", "latency_p95_s", "latency_p99_s",
    "ttft_p50_s", "ttft_p95_s", "ttft_p99_s",
    "decode_rate_p50_tokens_per_s", "prefill_rate_p50_tokens_per_s",
    # --- gate 4: in-flight ----------------------------------------------------------------
    # The span fields bracket the whole ff_cell subprocess, which runs its OWN single-stream
    # pre- and post-rate probes around the load; the _load_window fields cover only the
    # load's own rows and are the term gate 4 scores. Both are kept: a diluted figure that
    # is LABELLED diluted is evidence, a diluted figure presented as the cell is not.
    "slots_poll", "slot_busy_fraction", "both_slots_busy_fraction",
    "slots_poll_load_window", "slot_busy_fraction_load_window",
    "both_slots_busy_fraction_load_window",
    "metrics_before", "metrics_after", "n_busy_slots_per_decode_delta",
    # --- gate 7: prefill was real, not served from the prompt cache -------------------------
    "prefill_real", "prefill_cached",
    # --- gate 3: admission ------------------------------------------------------------------
    "commit_free_gb_before", "commit_free_gb_after", "budget_headroom", "over_admitted",
    # --- gate 5: board duty cycle against the frozen reference ---------------------------
    "reference", "power", "duty_cycle", "symmetry", "b70_stream",
    # --- gate 6: depth-0 ---------------------------------------------------------------------
    "depth0",
    # --- gate 8: thermal, off the same b70tools stream ----------------------------------------
    # ``thermal`` is the per-card/per-counter summary PLUS the thresholds it was scored
    # against and the per-card outcome. ``thermal_exceeded`` is the top-level flag: like
    # ``over_admitted`` it KEEPS the row and excludes it from the surface, never drops it.
    "thermal", "thermal_exceeded",
    # ``thermal_live`` is the LIVE watchdog's record: what it saw while the load was running,
    # and whether it stopped the cell. Gate 8 above scores a stream that has already finished --
    # it protects the dataset and cannot protect the cards. This field is the other half.
    "thermal_live",
    # --- instrument, NOT a gate: per-round launch skew from the server's own log -----------
    # The prereg's 2026-09-09 protocol addition. Nothing in GATES reads it and no status
    # depends on it; it exists so a bimodal cell says which mode it landed in instead of
    # having the event smeared into a variance.
    "launch_skew",
    # --- bookkeeping -------------------------------------------------------------------------
    "bearer", "ff_cell", "gates", "notes",
)


# ------------------------------------------------------------------ cell identity --
def cell_id(np_slots: int, depth: int, n: int, rep: int) -> str:
    return "np%d-p%d-c%d-r%d" % (np_slots, depth, n, rep)


def parse_cell_id(cell: str) -> dict:
    m = CELL_RE.match(cell or "")
    if not m:
        raise ValueError("cell %r is not np<np>-p<depth>-c<N>-r<rep>" % (cell,))
    return {k: int(v) for k, v in m.groupdict().items()}


def per_slot_ctx(np_slots: int) -> int:
    return TOTAL_CTX // max(1, np_slots)


def admissible(depth: int, np_slots: int) -> bool:
    """P8: at fixed -c 131072, raising -np shrinks per-slot context (64K/32K/16K)."""
    return depth <= per_slot_ctx(np_slots)


def repeats_for(np_slots: int, depth: int, n: int) -> int:
    """>=3 per cell; >=5 for the cells that decide P5 and P6, and for the noise floor.

    The noise-floor cell is N=2 / 512 (the card's Analysis plan). P5 is scored over the
    -np 2 surface at N>=4. P6 is scored at -np 8 / 512; the grid carries N=4 and N=8, the
    two that bracket its predicted knee of 4-6.
    """
    if np_slots == CARD_NP and depth == 512 and n == 2:
        return 5
    if np_slots == CARD_NP and n >= 4:
        return 5
    if np_slots == 8 and depth == 512 and n in (4, 8):
        return 5
    return 3


def is_depth0_cell(n: int, rep: int) -> bool:
    """One depth-0 capture per depth block at N=2 and N=16 -- the first repeat only.

    The ring holds ~48 min at the measured rate, so a capture per repeat would outrun it.
    """
    return n in DEPTH0_N and rep == 1


def plan_cells(phase: int = 1) -> list[str]:
    """Depth blocks OUTERMOST, N ascending -- so a warm rung is never measured cold."""
    cells: list[str] = []
    nps = (CARD_NP,) if phase == 1 else PHASE2_NP
    ns = PHASE1_N if phase == 1 else PHASE2_N
    for np_slots in nps:
        for depth in DEPTHS:
            if not admissible(depth, np_slots):
                continue
            for n in ns:
                for rep in range(1, repeats_for(np_slots, depth, n) + 1):
                    cells.append(cell_id(np_slots, depth, n, rep))
    return cells


# ------------------------------------------------------------------------ bearer --
def bearer_info(env: dict | None = None) -> dict:
    """The bearer's NAME and LENGTH. Never its value, in a receipt or a printed command."""
    env = os.environ if env is None else env
    value = env.get(BEARER_ENV) or ""
    return {"env": BEARER_ENV, "present": bool(value), "length": len(value),
            "passed_to_harness_as": HARNESS_KEY_ENV,
            "source": "launcher environment via hearth\\etc\\with-gateway-env.cmd; "
                      "hearth\\var\\gateway.cmd is never read by this module"}


def redacted_bearer(info: dict) -> str:
    return "%s=<redacted: %s, length=%d>" % (HARNESS_KEY_ENV, info["env"], info["length"])


def _token(env: dict | None = None) -> str | None:
    env = os.environ if env is None else env
    return env.get(BEARER_ENV) or None


def _rung_reader():
    """The passive rung reader, pinned to the LIVE checkout's keep-alive tail and baselines."""
    sys.path.insert(0, str(REPO))
    from hearth.health.rungstate import live_rung_state

    return lambda: live_rung_state(root=LIVE_ROOT)


# ------------------------------------------------------- warm-to-flatness (ADR-0043) --
def warm_to_flatness(measure, threshold_pct: float = FLATNESS_SPREAD_PCT,
                     max_iterations: int = FLATNESS_MAX_ITERATIONS) -> dict:
    """Call ``measure()`` until its ``repeat_spread_pct`` is at or under ``threshold_pct``.

    ``measure`` is a zero-argument callable returning ``ff_ratecheck.measure``'s dict. Every
    iteration is recorded, because the FIRST iteration's FIRST rep is the unwarmed rep-1
    that P7 predicts reads 65-90% of warm -- discarding it would discard the prediction.

    A failed measurement stops the loop and is recorded; the loop is capped so a rung that
    never settles produces a REFUSAL rather than an unbounded warm-up.
    """
    iterations: list[dict] = []
    flat = False
    error = None
    for _ in range(max(1, max_iterations)):
        m = measure()
        iterations.append(m)
        if not m.get("ok"):
            error = m.get("error")
            break
        spread = m.get("repeat_spread_pct")
        if spread is not None and spread <= threshold_pct:
            flat = True
            break
    first = iterations[0] if iterations else {}
    reps = first.get("decode_reps") or []
    return {
        "flat": flat,
        "threshold_pct": threshold_pct,
        "iterations": iterations,
        "iterations_used": len(iterations),
        "max_iterations": max(1, max_iterations),
        "unwarmed_rep1_tok_s": reps[0] if reps else None,
        "final_decode_tok_s": iterations[-1].get("decode_tok_s") if iterations else None,
        "final_spread_pct": iterations[-1].get("repeat_spread_pct") if iterations else None,
        "error": error,
        "note": "ADR-0043: a rung idle > ~60 s is not at its known-good rate. The first "
                "iteration's first rep is the unwarmed rep-1 P7 scores against warm.",
    }


# ------------------------------------------------------------- omen.yaml -np editing --
_NP_TOKEN = re.compile(r"(?<![\w-])-np\s+(\d+)")


def _model_block_bounds(lines: list[str], model_key: str) -> tuple[int, int]:
    key_re = re.compile(r'^\s{2}"?%s"?\s*:\s*$' % re.escape(model_key))
    start = None
    for i, line in enumerate(lines):
        if key_re.match(line):
            start = i
            break
    if start is None:
        raise ValueError("no model entry %r in the yaml" % (model_key,))
    sibling = re.compile(r'^\s{0,2}\S')
    for j in range(start + 1, len(lines)):
        if sibling.match(lines[j]) and lines[j].strip() and not lines[j].lstrip().startswith("#"):
            return start, j
    return start, len(lines)


def edit_np_yaml(text: str, np_slots: int, model_key: str = PRODUCTION_MODEL_KEY) -> tuple[str, int]:
    """Replace ONLY the ``-np <n>`` token inside ``model_key``'s block. Returns (text, old).

    Token-exact and reversible: nothing else in the file is touched, not even whitespace,
    so re-running with the previous value restores the file byte for byte. Zero or more
    than one ``-np`` inside the block is an error, never a guess -- a silent second match
    would leave production on a value nobody chose.
    """
    if int(np_slots) < 1:
        raise ValueError("-np must be >= 1")
    lines = text.splitlines(keepends=True)
    start, end = _model_block_bounds(lines, model_key)
    hits = [(i, m) for i in range(start, end) for m in [_NP_TOKEN.search(lines[i])] if m]
    if not hits:
        raise ValueError("no '-np <n>' token inside the %r block" % (model_key,))
    if len(hits) > 1:
        raise ValueError("%d '-np <n>' tokens inside the %r block; refusing to guess"
                         % (len(hits), model_key))
    i, m = hits[0]
    old = int(m.group(1))
    line = lines[i]
    lines[i] = line[:m.start(1)] + str(int(np_slots)) + line[m.end(1):]
    return "".join(lines), old


def read_np_yaml(text: str, model_key: str = PRODUCTION_MODEL_KEY) -> int:
    _, old = edit_np_yaml(text, 1, model_key)
    return old


def read_exact(path: Path) -> str:
    """Read without newline translation, so a write-back is byte-exact.

    ``Path.read_text`` translates CRLF to LF; writing that back would rewrite every line
    of omen.yaml while claiming to have changed one token. Token-exact means token-exact.
    """
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return handle.read()


def write_exact(path: Path, text: str) -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


# --------------------------------------------------------------- epoch boundaries --
def epoch_boundary_row(ts: str, np_slots: int, baseline_tok_s: float | None) -> dict:
    """A row shaped exactly like the one already on disk in rate-baselines.json.

    NOTE, stated rather than assumed: ``ff_cell.incumbent_epoch()`` does NOT read this
    list. It derives the incumbent's epoch from ``hearth\\var\\arc-serve.log`` (elapsed
    stamp subtracted from file mtime), because llama-server runs under an S4U task whose
    StartTime is inaccessible. ``epoch_boundaries`` is the human/ADR-0044 record of where
    one epoch ended and the next began; the shape below is the one the file already uses.
    """
    return {
        "ts": ts,
        "reason": "SAT-L1 Phase 2: production restarted to -np %d (-ub %d) via ArcServeRestart; "
                  "the -np change is a configuration change, so the prior baseline does not "
                  "carry across it (ADR-0044)." % (np_slots, UB),
        "baseline_preserved": False,
        "note": "Re-baselined by ff_ratecheck --set-baseline immediately after the real ready "
                "marker; new baseline_decode_tok_s=%s. Readings across this timestamp are two "
                "epochs, not one." % (baseline_tok_s,),
    }


def append_epoch_boundary(baselines_path: Path, row: dict) -> dict:
    doc = json.loads(Path(baselines_path).read_text(encoding="utf-8-sig"))
    doc.setdefault("epoch_boundaries", []).append(row)
    Path(baselines_path).write_text(json.dumps(doc, indent=2, ensure_ascii=False),
                                    encoding="utf-8")
    return doc


# ------------------------------------------------------------------ /slots, /metrics --
SPAN_VS_WINDOW_NOTE = ("span figures include ff_cell's single-stream pre/post probes; "
                       "load-window figures are the gate-4 term")


def poll_rows(samples: list) -> list:
    """Every ``/slots`` poll as one compact row: when, how many busy, how many slots.

    Kept, not reduced away. The span figures answer "how busy was the bracket"; only the
    per-poll rows can answer "which request waited" -- r4's last request sat 0.45 s behind
    a slot (TTFT 0.510 s against 20-90 ms elsewhere, server-side work normal) and
    ``slots_poll.polls == 19`` could not say so.

    Accepts the poller's raw shape (``{"t": ..., "slots": [...]}`` or ``{"t": ...,
    "error": ...}``) and rows already in this compact shape, so a receipt's own
    ``slots_poll.samples`` can be re-windowed offline without the raw slot detail.
    """
    rows = []
    for s in samples or ():
        if not isinstance(s, dict):
            continue
        t_wall = s.get("t_wall", s.get("t"))
        slots = s.get("slots")
        if isinstance(slots, list):
            busy = sum(1 for slot in slots if isinstance(slot, dict) and slot.get("is_processing"))
            rows.append({"t_wall": t_wall, "n_busy": busy, "n_slots": len(slots), "ok": True})
            continue
        if "n_busy" in s or "n_slots" in s:
            row = {"t_wall": t_wall, "n_busy": s.get("n_busy"), "n_slots": s.get("n_slots"),
                   "ok": bool(s.get("ok", True))}
        else:
            row = {"t_wall": t_wall, "n_busy": None, "n_slots": None, "ok": False}
        if s.get("error") is not None:
            row["error"] = s.get("error")
            row["ok"] = False
        rows.append(row)
    return rows


def _busy_fields(rows: list) -> dict:
    """The occupancy figures over compact poll rows. OK polls only; none -> ``None``.

    ``null != 0`` is a campaign invariant: a window nothing was polled in is unknown, not
    idle.
    """
    ok = [r for r in rows if r.get("ok")]
    errors = [r for r in rows if not r.get("ok")]
    out = {"polls": len(rows), "ok_polls": len(ok), "error_polls": len(errors),
           "slots_seen": None, "any_busy_fraction": None, "all_busy_fraction": None,
           "mean_busy_slots": None, "busy_slot_fraction": None,
           "error_sample": (errors[0].get("error") if errors else None)}
    if not ok:
        return out
    n = len(ok)
    busy_counts = [int(r.get("n_busy") or 0) for r in ok]
    totals = [int(r.get("n_slots") or 0) for r in ok]
    out.update({
        "slots_seen": max(totals) if totals else None,
        "any_busy_fraction": round(sum(1 for b in busy_counts if b >= 1) / n, 4),
        "all_busy_fraction": round(
            sum(1 for b, t in zip(busy_counts, totals) if t and b == t) / n, 4),
        "mean_busy_slots": round(sum(busy_counts) / n, 4),
        "busy_slot_fraction": round(
            sum((b / t) if t else 0.0 for b, t in zip(busy_counts, totals)) / n, 4),
    })
    return out


def slot_busy(samples: list) -> dict:
    """Slot occupancy from 1 Hz ``/slots`` polls, over the WHOLE poller span.

    A sample is ``{"t": <wall epoch>, "slots": [...]}`` or ``{"t": ..., "error": "..."}``.
    Fractions are over the OK polls only; with none, every fraction is ``None`` rather
    than 0.0 -- ``null != 0`` is a campaign invariant.

    The span is the whole ``ff_cell`` subprocess, which runs its own single-stream pre- and
    post-rate probes around the load, so these fractions are DILUTED: 0.47-0.53 over the
    first live repeats where the load itself held both slots. ``window_slot_busy`` is the
    gate-4 term. Every per-poll row rides along in ``samples``.
    """
    rows = poll_rows(samples)
    out = _busy_fields(rows)
    out["samples"] = rows
    return out


def window_slot_busy(samples: list, t0: float | None, t1: float | None) -> dict:
    """The same occupancy figures over ``t0 <= t_wall <= t1`` only, plus ``n_samples``.

    Pure: no clock, no I/O. ``t0``/``t1`` are wall epoch seconds -- for a cell they are
    ``min(started_at)`` and ``max(completed_at)`` over the load's OWN rows, so the
    single-stream probes ff_cell runs around the load fall outside the window. Either
    bound missing selects nothing and every figure is ``None``: an unknown window is not
    an empty one.
    """
    rows = poll_rows(samples)
    reason = None
    if t0 is None or t1 is None:
        selected = []
        reason = ("no load window: the cell's rows carry no started_at/completed_at pair, so "
                  "the polls cannot be attributed to the load")
    else:
        selected = [r for r in rows
                    if isinstance(r.get("t_wall"), (int, float)) and t0 <= r["t_wall"] <= t1]
        if not selected:
            reason = "no /slots poll fell inside the load window"
    out = _busy_fields(selected)
    out["n_samples"] = len(selected)
    out["t0"] = t0
    out["t1"] = t1
    out["window_s"] = round(t1 - t0, 3) if t0 is not None and t1 is not None else None
    out["reason"] = reason
    out["definition"] = ("polls inside [min(started_at), max(completed_at)] over the load's "
                         "own rows; " + SPAN_VS_WINDOW_NOTE)
    return out


def load_window_bounds(rows: list) -> tuple:
    """``(min(started_at), max(completed_at))`` over harness rows, as wall epoch seconds.

    ``(None, None)`` when either end is underivable -- half a window is not a window.
    """
    starts = [t for t in (_iso_epoch(r.get("started_at")) for r in rows or ()) if t is not None]
    ends = [t for t in (_iso_epoch(r.get("completed_at")) for r in rows or ()) if t is not None]
    if not starts or not ends:
        return None, None
    return min(starts), max(ends)


_PROM = re.compile(r"^(?P<name>[A-Za-z_:][\w:]*)(?P<labels>\{[^}]*\})?\s+(?P<value>[-+0-9.eEnaN]+)\s*$")


def parse_prometheus(text: str) -> dict:
    """Bare-name Prometheus samples. Labelled series keep their label string as the key."""
    out: dict = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _PROM.match(line)
        if not m:
            continue
        key = m.group("name") + (m.group("labels") or "")
        try:
            out[key] = float(m.group("value"))
        except ValueError:
            continue
    return out


BUSY_SLOTS_SERIES = "llamacpp:n_busy_slots_per_decode"


def busy_slots_delta(before: dict | None, after: dict | None) -> float | None:
    if not before or not after:
        return None
    b, a = before.get(BUSY_SLOTS_SERIES), after.get(BUSY_SLOTS_SERIES)
    if b is None or a is None:
        return None
    return round(a - b, 6)


PROMPT_TOKENS_SERIES = "llamacpp:prompt_tokens_total"
PROMPT_TOKENS_CACHED_SERIES = "llamacpp:prompt_tokens_cached_total"

#: Gate 7 passes when the server processed at least this much of the prompt tokens the
#: cell's own rows asked for. Not 1.0: the bracket is a scrape pair, and a request may
#: legitimately share a prefix with its immediate predecessor inside one slot.
PREFILL_REAL_MIN_FRACTION = 0.9


def _series_delta(before: dict | None, after: dict | None, key: str, default=None):
    if not isinstance(before, dict) or not isinstance(after, dict):
        return default
    b, a = before.get(key), after.get(key)
    if b is None or a is None:
        return default
    return a - b


def prefill_real(before: dict | None, after: dict | None, rows: list,
                 min_fraction: float = PREFILL_REAL_MIN_FRACTION) -> dict:
    r"""Did the server actually prefill this cell's prompts, or serve them from its cache?

    The harness's own ``_performed_full_prefill`` check NULLS the prefill rate on a cache
    hit; that is the right thing for a rate and the wrong thing for this surface, whose
    size axis (512 / 8K / 32K prompts) IS prefill. So the question is asked of the SERVER's
    own counters rather than of the harness rows:

      ``uncached``  delta of ``llamacpp:prompt_tokens_total``   -- tokens really processed
      ``cached``    delta of ``llamacpp:prompt_tokens_cached_total`` -- tokens served warm
                    (0 when the build carries no such series -- absent, not unknown)
      ``expected``  the sum of ``prompt_tokens`` over the cell's own load rows

    Measured on the first live repeats (np2-p512-c2-r4): uncached moved 64 while cached
    moved 3,073 for 6 x 440 = 2,640 expected tokens -- fraction 0.024, a cell whose prefill
    was never measured.

    NOTHING is subtracted for ff_cell's single-stream pre/post rate probes, which are
    inside the same scrape bracket and prefill the ratecheck prompt: an unknown correction
    is not applied silently. Their contribution is reported as ``uncached - expected`` when
    that is positive, so a reader can see the bracket is wider than the load.
    """
    expected = None
    counted = 0
    for r in rows or ():
        value = (r or {}).get("prompt_tokens")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            expected = (expected or 0) + int(value)
            counted += 1
    uncached = _series_delta(before, after, PROMPT_TOKENS_SERIES)
    cached = _series_delta(before, after, PROMPT_TOKENS_CACHED_SERIES, default=0.0)
    doc = {"uncached": uncached, "cached": cached, "expected": expected, "fraction": None,
           "outcome": "null", "reason": None, "min_fraction": min_fraction,
           "rows_counted": counted, "probe_contribution": None,
           "series": {"uncached": PROMPT_TOKENS_SERIES, "cached": PROMPT_TOKENS_CACHED_SERIES},
           "note": ("the /metrics bracket also contains ff_cell's single-stream pre/post rate "
                    "probes, which prefill the ratecheck prompt; nothing is subtracted for "
                    "them -- their contribution shows as probe_contribution when positive")}
    if uncached is None:
        doc["reason"] = ("no %s in both scrapes -- prefill is unknown, never assumed real"
                         % PROMPT_TOKENS_SERIES)
        return doc
    if not expected:
        doc["reason"] = "no prompt_tokens on the cell's load rows -- nothing to compare against"
        return doc
    doc["fraction"] = round(uncached / expected, 4)
    if uncached > expected:
        doc["probe_contribution"] = round(uncached - expected, 3)
    if uncached >= min_fraction * expected:
        doc["outcome"] = "pass"
        doc["reason"] = ("%s processed %s of the %s prompt tokens the cell's rows asked for "
                         "(>= %.2f)" % (PROMPT_TOKENS_SERIES, uncached, expected, min_fraction))
    else:
        doc["outcome"] = "fail"
        doc["reason"] = ("prefill was served from the prompt cache: %s tokens processed, %s "
                         "cached, against %s expected (%.4f < %.2f)"
                         % (uncached, cached, expected, doc["fraction"], min_fraction))
    return doc


# ------------------------------------------- per-round launch skew (instrument only) --
# WHY THIS EXISTS. The prereg's 2026-09-09 FINDING: the noise-floor cell's jobs/hour is
# BIMODAL, not scattered. Every affected cell has exactly one round in which the server
# launched the two concurrent requests ~222 ms apart instead of ~0.2 ms; that round's
# prefill drops to the single-stream rate and its decode falls, costing the cell ~5%. It is
# server-side -- the harness's own rows put both requests' ``started_at`` within 0.0-12.5 ms.
# The card's protocol addition is that EVERY cell records its per-round launch skew, so a
# point on the surface says whether it hit the event instead of having it averaged into a
# variance. This is INSTRUMENTATION: NO gate reads ``launch_skew`` -- gate 8 exists but it is
# the thermal gate and reads only the temperature counters -- and a delayed round never
# changes a cell's status.
#
# TIMEBASE TRAP. ``hearth\var\arc-serve.log`` (llama-server at ``-lv 5``) stamps every line
# with MINUTES.SS.mmm.uuu of SERVER UPTIME -- not wall clock, and the minutes field runs past
# 60 without rolling into hours (the live log reaches 1006.51.450.090). There is no wall
# stamp on these lines at all, so the uptime clock must be anchored to wall time before a
# cell's own window can be located in it. See ``anchor_serve_log``.

#: A launch line:
#:   991.07.423.118 I slot  operator (): id  0 | task 30994 | new prompt, n_ctx_slot = 65536,
#:                                                            n_keep = 0, task.n_tokens = 440
#: The function name column is llama.cpp's truncated ``launch_slot_with_task`` and renders as
#: ``operator ()``; it is matched loosely on purpose. ``[^\n]`` keeps a match inside ONE
#: physical line -- the log does concatenate records without a newline (a ``D No parser
#: definition detected...`` line runs straight into the next timestamp), and finditer must not
#: be allowed to straddle that seam.
_SERVE_TS = r"(?P<minutes>\d+)\.(?P<seconds>\d{2})\.(?P<millis>\d{3})\.(?P<micros>\d{3})"
_SERVE_LAUNCH = re.compile(
    _SERVE_TS + r"\s+\w\s+slot\s+[^\n|]*?id\s+(?P<slot>\d+)\s*\|\s*task\s+(?P<task>\d+)\s*\|"
                r"\s*new prompt\b[^\n]*?task\.n_tokens\s*=\s*(?P<n>\d+)")
#: A release line:
#:   991.11.042.317 I slot      release: id  0 | task 30994 | stop processing: n_tokens = 106
_SERVE_RELEASE = re.compile(
    _SERVE_TS + r"\s+\w\s+slot\s+release:\s*id\s+(?P<slot>\d+)\s*\|\s*task\s+(?P<task>\d+)\s*\|"
                r"\s*stop processing[^\n]*?n_tokens\s*=\s*(?P<n>\d+)")

#: Read this much of the log's tail by default. At ``-lv 5`` under load the server writes
#: ~1 MB/min, so 16 MiB is ~15 min of BUSY logging -- comfortably more than one cell's load
#: window -- while never loading the whole 70 MB file.
SERVE_LOG_TAIL_BYTES = 16 * 1024 * 1024
SERVE_LOG_TAIL_MAX_BYTES = 96 * 1024 * 1024

#: A round is "delayed" above this skew. Chosen to sit far above the clean 0.2 ms case and
#: far below the 222 ms event, so neither jitter nor the event can land near the edge.
LAUNCH_SKEW_THRESHOLD_MS = 50.0

#: The anchor's own tolerances. ``OFFSET_TOLERANCE_S`` is the one that matters: repeats of
#: this cell run ~5 min apart and each is preceded by a DISCARDED warm load of the same shape
#: and duration, so duration matching ALONE is degenerate -- it happily anchors a cell onto
#: its own discarded warm load 16 s earlier (measured, 2026-09-09, on np2-p512-c2-r9). The
#: coarse mtime offset is what separates them, and it is worth ~20 ms in practice.
ANCHOR_OFFSET_TOLERANCE_S = 5.0
ANCHOR_ACCEPT_RESIDUAL_S = 1.0
ANCHOR_DISTINCT_OFFSET_S = 0.5
ANCHOR_SEPARATION_S = 0.2

#: The launch window is padded BEFORE the load's first ``started_at`` so the lone warm
#: request that precedes the measured pair is visible rather than silently cropped -- it is
#: the evidence that refuted "the instrument was contending with itself". It is not a round,
#: and ``round_launch_skews`` is required to keep it out of one.
LAUNCH_WINDOW_PAD_BEFORE_S = 1.0
LAUNCH_WINDOW_PAD_AFTER_S = 0.5


def _serve_uptime_s(match) -> float:
    """MINUTES.SS.mmm.uuu of server uptime -> seconds. Minutes are NOT capped at 60."""
    return (int(match.group("minutes")) * 60 + int(match.group("seconds"))
            + int(match.group("millis")) / 1e3 + int(match.group("micros")) / 1e6)


def parse_serve_log_launches(text_or_lines, *, n_tokens=None) -> list:
    """``new prompt`` / ``release ... stop processing`` events out of an arc-serve.log tail.

    Returns ``{"uptime_s", "slot", "task", "n_tokens", "event"}`` per event, sorted by
    ``uptime_s``. Anything the two patterns do not understand is skipped in silence -- the
    log is 99% lines this instrument has no opinion about.

    ``n_tokens`` FILTERS ASYMMETRICALLY, and deliberately. On a launch line the number is
    ``task.n_tokens``, the PROMPT the slot is about to process; on a release line the same
    key names the tokens GENERATED (106 for a 440-token prompt). Filtering releases by their
    own number would therefore drop exactly the releases belonging to the launches that were
    kept. So: launches are filtered on their own ``task.n_tokens``, and a release is kept
    when its TASK id belongs to a kept launch. Each event still reports the number its own
    line carried, which is why a release's ``n_tokens`` is not the launch's.
    """
    if isinstance(text_or_lines, (bytes, bytearray)):
        text = bytes(text_or_lines).decode("utf-8", "replace")
    elif isinstance(text_or_lines, str):
        text = text_or_lines
    else:
        text = "\n".join(str(line) for line in text_or_lines)

    events, kept_tasks = [], set()
    for match in _SERVE_LAUNCH.finditer(text):
        count = int(match.group("n"))
        if n_tokens is not None and count != n_tokens:
            continue
        task = int(match.group("task"))
        kept_tasks.add(task)
        events.append({"uptime_s": _serve_uptime_s(match), "slot": int(match.group("slot")),
                       "task": task, "n_tokens": count, "event": "launch"})
    for match in _SERVE_RELEASE.finditer(text):
        task = int(match.group("task"))
        if n_tokens is not None and task not in kept_tasks:
            continue
        events.append({"uptime_s": _serve_uptime_s(match), "slot": int(match.group("slot")),
                       "task": task, "n_tokens": int(match.group("n")), "event": "release"})
    events.sort(key=lambda event: (event["uptime_s"], event["event"], event["task"]))
    return events


def read_serve_log_tail(path, max_bytes: int = SERVE_LOG_TAIL_BYTES) -> dict:
    """Seek to ``max_bytes`` from the end and decode only that. Never loads the whole file.

    ``{"text", "path", "bytes_read", "file_bytes", "mtime_epoch", "truncated", "reason"}``.
    A missing or unreadable log is a LOUD NULL: ``text`` is ``None`` and ``reason`` says why.
    """
    doc = {"text": None, "path": str(path), "bytes_read": 0, "file_bytes": None,
           "mtime_epoch": None, "truncated": None, "reason": None}
    try:
        target = Path(path)
        stat = target.stat()
        doc["file_bytes"] = stat.st_size
        doc["mtime_epoch"] = stat.st_mtime
        want = max(0, min(int(max_bytes), stat.st_size))
        with io.open(target, "rb") as handle:
            handle.seek(stat.st_size - want)
            raw = handle.read(want)
    except OSError as exc:
        doc["reason"] = "serve log unreadable: %s" % exc
        return doc
    doc["bytes_read"] = len(raw)
    doc["truncated"] = len(raw) < (doc["file_bytes"] or 0)
    doc["text"] = raw.decode("utf-8", "replace")
    if not doc["text"].strip():
        doc["reason"] = "serve log tail is empty"
        doc["text"] = None
    return doc


def round_launch_skews(launches, *, expected_concurrency: int,
                       threshold_ms: float = LAUNCH_SKEW_THRESHOLD_MS) -> dict:
    """Group launch events into rounds and report the launch skew of each.

    ``launches`` may be the mixed launch/release list ``parse_serve_log_launches`` returns;
    the release events are used, not ignored.

    THE GROUPING RULE, stated so it can be argued with. Walking the launches in uptime
    order, a NEW round starts when any of these holds:

      (a) the current round already holds ``expected_concurrency`` launches; or
      (b) the gap to the previous launch exceeds ``split_gap_s`` = half the MEDIAN ROUND
          DURATION (median of ``release - launch`` over tasks that have both events); or
      (c) some launch already in the current round had RELEASED before this one launched --
          they never ran concurrently, so they were not a round.

    (c) is the rule that earns its keep. A cell is preceded by a single warm request that is
    not part of any round, and on the measured cells it launches only ~0.6 s ahead of the
    first real round -- far inside any gap threshold derived from a 3.3 s round, so (b)
    alone would fold it into round 1 and shift every round by one launch. It released ~25 ms
    before that round launched, so (c) separates it correctly and by physics rather than by
    a tuned constant.

    With no releases to measure, ``split_gap_s`` is ``None``, only (a) applies, and
    ``reason`` says so -- a degraded reading that announces itself.

    A group of fewer than 2 launches has no skew and is NOT a round: it is counted in
    ``partial_groups``. A group split by (c) also increments ``serialized_splits``, so a
    regime in which the server stopped batching entirely shows up as splits rather than as a
    quietly clean ``max_skew_ms``.
    """
    events = list(launches or ())
    starts = sorted((e for e in events if e.get("event") == "launch"),
                    key=lambda e: e["uptime_s"])
    released: dict = {}
    for event in events:
        if event.get("event") == "release":
            task = event.get("task")
            if task not in released or event["uptime_s"] < released[task]:
                released[task] = event["uptime_s"]

    doc = {"rounds": [], "max_skew_ms": None, "median_skew_ms": None, "delayed_rounds": 0,
           "threshold_ms": float(threshold_ms), "reason": None,
           "expected_concurrency": int(expected_concurrency), "launches": len(starts),
           "split_gap_s": None, "partial_groups": 0, "serialized_splits": 0,
           "rule": "new round on (a) round full, (b) gap > half the median round duration, "
                   "(c) a member of the current round released before this launch"}
    reasons = []
    if not starts:
        doc["reason"] = "no launches to group"
        return doc
    if expected_concurrency < 1:
        doc["reason"] = "expected_concurrency %r is not a concurrency" % (expected_concurrency,)
        return doc

    durations = [released[s["task"]] - s["uptime_s"] for s in starts
                 if s["task"] in released and released[s["task"]] > s["uptime_s"]]
    if durations:
        doc["split_gap_s"] = 0.5 * statistics.median(durations)
    else:
        reasons.append("no release events for these launches -- rounds are grouped by "
                       "concurrency alone, so a round boundary can only be inferred from "
                       "count")

    split_gap = doc["split_gap_s"]
    groups: list = []
    current: list = []
    for start in starts:
        if current:
            if len(current) >= expected_concurrency:
                groups.append(current)
                current = []
            elif split_gap is not None and (start["uptime_s"] - current[-1]["uptime_s"]) > split_gap:
                groups.append(current)
                current = []
            elif any(released.get(member["task"], float("inf")) <= start["uptime_s"]
                     for member in current):
                doc["serialized_splits"] += 1
                groups.append(current)
                current = []
        current.append(start)
    if current:
        groups.append(current)

    skews = []
    for group in groups:
        if len(group) < 2:
            doc["partial_groups"] += 1
            continue
        skew_ms = (group[-1]["uptime_s"] - group[0]["uptime_s"]) * 1000.0
        skews.append(skew_ms)
        doc["rounds"].append({"index": len(doc["rounds"]), "skew_ms": round(skew_ms, 4),
                              "slots": [member["slot"] for member in group],
                              "tasks": [member["task"] for member in group],
                              "t0_uptime_s": round(group[0]["uptime_s"], 6),
                              "size": len(group),
                              "complete": len(group) == expected_concurrency})
    if skews:
        doc["max_skew_ms"] = round(max(skews), 4)
        doc["median_skew_ms"] = round(statistics.median(skews), 4)
        doc["delayed_rounds"] = sum(1 for value in skews if value > threshold_ms)
    else:
        reasons.append("no group held 2 or more launches -- there is no round to time")
    if expected_concurrency == 1:
        reasons.append("expected_concurrency is 1: a round of one request has no launch "
                       "skew to measure, and every skew here is 0 by construction")
    if doc["partial_groups"]:
        reasons.append("%d group(s) held fewer than 2 launches (the lone warm request that "
                       "precedes a cell is the expected one)" % doc["partial_groups"])
    doc["reason"] = "; ".join(reasons) or None
    return doc


def anchor_serve_log(events, rows, *, log_end_epoch, log_end_uptime_s=None,
                     prompt_tokens=None,
                     offset_tolerance_s: float = ANCHOR_OFFSET_TOLERANCE_S,
                     accept_residual_s: float = ANCHOR_ACCEPT_RESIDUAL_S) -> dict:
    """Solve ``offset = wall_epoch - server_uptime_s`` for ONE cell, or refuse to.

    THE METHOD, in the order the terms are trusted:

      1. COARSE, from the file itself. The newest slot event in the tail is, to within a
         write flush, the last thing written to the log, so
         ``offset0 = log mtime - max(uptime_s)``. Measured 2026-09-09 against four recorded
         cells this is good to ~20 ms -- the client-to-server latency of the first request.
      2. CANDIDATES. Launches whose IMPLIED offset (``first started_at - launch uptime``)
         sits within ``offset_tolerance_s`` of ``offset0``. This step is load-bearing: each
         cell is preceded by a DISCARDED warm load of identical shape ~16 s earlier, and
         without the coarse constraint the fit below anchors onto it just as happily
         (r9 anchored 16.6 s early, residual 0.0243 s vs the true 0.0218 s -- no separation).
      3. FIT. For each candidate, offset it and ask, for EVERY request in the cell, how far
         its predicted uptime lands from the nearest launch. The score is the WORST of those
         residuals, so a candidate has to explain the whole cell, not its first request.
      4. CONFIRM against the duration the brief names: ``release - launch`` for the winning
         task against that request's own ``latency_s``, reported as ``duration_residual_s``.
      5. REFUSE. If the best score exceeds ``accept_residual_s``, or another candidate at a
         materially different offset scores within ``ANCHOR_SEPARATION_S`` of it, the anchor
         is AMBIGUOUS and ``offset`` is ``None`` with a reason. It is never guessed.
    """
    doc = {"offset": None, "method": None, "coarse_offset": None, "log_end_uptime_s": None,
           "log_end_epoch": log_end_epoch, "candidates": 0, "residual_s": None,
           "runner_up_residual_s": None, "duration_residual_s": None,
           "anchor_task": None, "anchor_uptime_s": None, "requests": 0, "reason": None,
           # True only when the failure is "this window of the log does not reach the cell",
           # which a bigger tail can fix. An ambiguous or badly-fitting anchor sets it False:
           # reading more log would not make either of those any more true.
           "coverage_limited": False}
    doc["method"] = ("offset = log mtime - newest slot-event uptime (coarse, ~20 ms), then "
                     "the launch within %.1f s of that offset whose whole-cell worst "
                     "residual is smallest; refused if > %.1f s or ambiguous"
                     % (offset_tolerance_s, accept_residual_s))
    launches = [e for e in (events or ()) if e.get("event") == "launch"]
    if not events and log_end_uptime_s is None:
        doc["reason"] = "no slot events parsed from the serve log tail"
        return doc
    # The log's END is the whole file's newest slot event -- NOT the newest of the events
    # that survived an ``n_tokens`` filter, which can sit minutes short of it and would
    # silently bias the coarse offset by exactly that much.
    doc["log_end_uptime_s"] = (float(log_end_uptime_s) if log_end_uptime_s is not None
                               else max(e["uptime_s"] for e in events))
    if log_end_epoch is None:
        doc["reason"] = "no wall stamp for the log's end -- the uptime clock cannot be anchored"
        return doc
    doc["coarse_offset"] = log_end_epoch - doc["log_end_uptime_s"]

    starts, latencies = [], []
    for row in rows or ():
        epoch = _iso_epoch(row.get("started_at"))
        if epoch is None:
            continue
        starts.append(epoch)
        latencies.append(row.get("latency_s"))
    order = sorted(range(len(starts)), key=lambda i: starts[i])
    starts = [starts[i] for i in order]
    latencies = [latencies[i] for i in order]
    doc["requests"] = len(starts)
    if not starts:
        doc["reason"] = "the cell's rows carry no parsable started_at"
        return doc
    matching = [l for l in launches
                if prompt_tokens is None or l.get("n_tokens") == prompt_tokens]
    if not matching:
        doc["coverage_limited"] = True
        doc["reason"] = ("no launch in the tail carries task.n_tokens = %s -- the cell's own "
                         "prompt size is not in this window of the log" % (prompt_tokens,))
        return doc

    predicted_first = starts[0] - doc["coarse_offset"]
    candidates = [l for l in matching
                  if abs(l["uptime_s"] - predicted_first) <= offset_tolerance_s]
    doc["candidates"] = len(candidates)
    if not candidates:
        doc["coverage_limited"] = True
        doc["reason"] = ("no %s-token launch within %.1f s of the coarse anchor (predicted "
                         "uptime %.3f s); the tail may not cover this cell"
                         % (prompt_tokens, offset_tolerance_s, predicted_first))
        return doc

    scored = []
    for candidate in candidates:
        offset = starts[0] - candidate["uptime_s"]
        worst = max(min(abs(l["uptime_s"] - (start - offset)) for l in matching)
                    for start in starts)
        scored.append((worst, offset, candidate))
    scored.sort(key=lambda item: item[0])
    best_score, best_offset, best = scored[0]
    doc["residual_s"] = round(best_score, 6)
    runner_up = next((item for item in scored[1:]
                      if abs(item[1] - best_offset) > ANCHOR_DISTINCT_OFFSET_S), None)
    if runner_up is not None:
        doc["runner_up_residual_s"] = round(runner_up[0], 6)
    if best_score > accept_residual_s:
        # Retryable: the commonest way to fit the FIRST request and miss the LAST is a tail
        # that starts partway through the cell. A bigger read can fix that, so say so.
        doc["coverage_limited"] = True
        doc["reason"] = ("best whole-cell residual %.4f s exceeds %.2f s -- nothing in this "
                         "log window explains the cell" % (best_score, accept_residual_s))
        return doc
    if runner_up is not None and (runner_up[0] - best_score) < ANCHOR_SEPARATION_S:
        doc["reason"] = ("ambiguous anchor: a candidate %.3f s away scores %.4f s against "
                         "the best %.4f s (separation < %.2f s)"
                         % (abs(runner_up[1] - best_offset), runner_up[0], best_score,
                            ANCHOR_SEPARATION_S))
        return doc

    released = {}
    for event in events:
        if event.get("event") == "release":
            task = event.get("task")
            if task not in released or event["uptime_s"] < released[task]:
                released[task] = event["uptime_s"]
    if best["task"] in released and isinstance(latencies[0], (int, float)):
        duration = released[best["task"]] - best["uptime_s"]
        doc["duration_residual_s"] = round(abs(duration - float(latencies[0])), 6)
    doc["offset"] = best_offset
    doc["anchor_task"] = best["task"]
    doc["anchor_uptime_s"] = round(best["uptime_s"], 6)
    return doc


def launch_skew_for_cell(rows, *, expected_concurrency: int, log_path=SERVE_LOG,
                         max_bytes: int = SERVE_LOG_TAIL_BYTES,
                         threshold_ms: float = LAUNCH_SKEW_THRESHOLD_MS) -> dict:
    """The receipt's ``launch_skew`` field: skews for THIS cell's rounds, or a loud null.

    ``{"rounds": None, "reason": "..."}`` whenever the log is missing, the cell's prompt size
    is absent from the tail, or the anchor cannot be established -- never an exception, never
    a silent zero. A recorded delayed round is an OBSERVATION, not a failure: no gate reads
    this field.
    """
    doc = {"rounds": None, "reason": None, "log_path": str(log_path),
           "log_bytes_read": None, "log_file_bytes": None, "tail_attempts": [], "anchor": None,
           "prompt_tokens": None, "expected_concurrency": int(expected_concurrency),
           "window_uptime_s": None, "launches_in_window": None,
           "threshold_ms": float(threshold_ms),
           "note": "instrument only -- a delayed round is a recorded observation, not a "
                   "failure, and no gate reads this field"}

    prompt_tokens = None
    for row in rows or ():
        value = row.get("prompt_tokens")
        if isinstance(value, (int, float)) and value > 0:
            prompt_tokens = int(value)
            break
    doc["prompt_tokens"] = prompt_tokens
    if prompt_tokens is None:
        doc["reason"] = ("the cell's rows report no prompt_tokens -- the launch lines cannot "
                         "be narrowed to this cell's own requests")
        return doc

    window_t0, window_t1 = load_window_bounds(rows)
    if window_t0 is None or window_t1 is None:
        doc["reason"] = "no load window over the cell's rows -- half a window is not a window"
        return doc

    # Read the tail, and GROW IT -- bounded, at most twice -- only when the anchor's own
    # failure says the window did not reach back to this cell. An ambiguous or badly-fitting
    # anchor is never retried: more log cannot make a degenerate fit less degenerate.
    size = int(max_bytes)
    events: list = []
    anchor: dict = {}
    while True:
        tail = read_serve_log_tail(log_path, size)
        doc["log_bytes_read"] = tail["bytes_read"]
        doc["log_file_bytes"] = tail["file_bytes"]
        doc["tail_attempts"] = (doc.get("tail_attempts") or []) + [tail["bytes_read"]]
        if tail["text"] is None:
            doc["reason"] = tail["reason"] or "serve log unreadable"
            return doc
        end_uptime = max((e["uptime_s"] for e in parse_serve_log_launches(tail["text"])),
                         default=None)
        if end_uptime is None:
            doc["reason"] = "no slot events in the serve log tail"
            return doc
        events = parse_serve_log_launches(tail["text"], n_tokens=prompt_tokens)
        anchor = anchor_serve_log(events, rows, log_end_epoch=tail["mtime_epoch"],
                                  log_end_uptime_s=end_uptime, prompt_tokens=prompt_tokens)
        if anchor.get("offset") is not None or not anchor.get("coverage_limited"):
            break
        if not tail["truncated"] or size >= SERVE_LOG_TAIL_MAX_BYTES:
            break
        size = min(size * 4, SERVE_LOG_TAIL_MAX_BYTES)
    doc["anchor"] = anchor
    if anchor.get("offset") is None:
        doc["reason"] = "anchor failed: %s" % (anchor.get("reason") or "unstated")
        return doc

    offset = anchor["offset"]
    lo = window_t0 - offset - LAUNCH_WINDOW_PAD_BEFORE_S
    hi = window_t1 - offset + LAUNCH_WINDOW_PAD_AFTER_S
    doc["window_uptime_s"] = [round(lo, 6), round(hi, 6)]
    in_window = [e for e in events
                 if e["event"] == "launch" and lo <= e["uptime_s"] <= hi]
    doc["launches_in_window"] = len(in_window)
    if not in_window:
        doc["reason"] = ("the anchor held but no %s-token launch falls inside the cell's own "
                         "load window" % prompt_tokens)
        return doc
    # Pair each in-window launch with its OWN first following release, rather than keeping
    # every event that shares a task id. Live task ids are unique and monotonic so the two
    # are the same set -- but an id that ever repeated would otherwise drag a different
    # cell's rounds into this one's, which is the class of error this instrument exists to
    # avoid making.
    releases = [e for e in events if e["event"] == "release"]
    scoped = list(in_window)
    for launch in in_window:
        following = [e for e in releases
                     if e["task"] == launch["task"] and e["uptime_s"] >= launch["uptime_s"]]
        if following:
            scoped.append(min(following, key=lambda e: e["uptime_s"]))
    skews = round_launch_skews(scoped, expected_concurrency=expected_concurrency,
                               threshold_ms=threshold_ms)
    doc.update(skews)
    doc["log_path"] = str(log_path)
    return doc


# ---------------------------------------------------- b70tools stream -> duty cycle --
def card_intervals(events_path: Path, counter: str = "gpu") -> dict:
    """Per-B70 (t0, t1, watts) intervals, the duty-cycle companion to reduce_stream.

    Same parse and same derivation as ``sat_reference_capture.reduce_stream`` -- watts are
    dJ/dt between consecutive ticks of ``<counter>.energy_j_counter``, and ``t`` is
    NANOSECONDS on the perf_counter clock. This keeps the per-interval timestamps that
    reduce_stream reduces away, because duty cycle is a fraction of WALL TIME inside a
    window, not a percentile.
    """
    name = "%s.energy_j_counter" % counter
    samples: dict = {}
    ident: dict = {}
    present: set = set()
    with Path(events_path).open(encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("k") == "ai":
                ident[row.get("a")] = {"desc": row.get("desc") or "", "bdf": row.get("bdf")}
            elif row.get("k") == "ms" and str(row.get("n", "")).endswith("energy_j_counter"):
                present.add(row["n"])
                if row["n"] == name:
                    samples.setdefault(row["a"], []).append((int(row["t"]), float(row["v"])))
    if name not in present:
        raise RuntimeError("stream carries no %r; present: %s" % (name, sorted(present)))
    cards: dict = {}
    for adapter, rows in samples.items():
        info = ident.get(adapter) or {}
        if "B70" not in (info.get("desc") or ""):
            continue  # the iGPU is not part of the reference
        rows.sort()
        intervals = []
        for i in range(len(rows) - 1):
            (t0, v0), (t1, v1) = rows[i], rows[i + 1]
            if t1 <= t0:
                continue
            intervals.append((t0, t1, (v1 - v0) / ((t1 - t0) / NS)))
        cards[adapter] = {"adapter": adapter, "desc": info.get("desc"), "bdf": info.get("bdf"),
                          "intervals": intervals}
    return cards


def load_reference(receipt_path: Path = REFERENCE_RECEIPT) -> dict:
    """The FROZEN SAT-L1 saturation reference, read from its receipt -- never hardcoded.

    Per-card burst p50 W (``power.cards.<adapter>.burst.p50_w``). The receipt keys cards by
    b70tools adapter id, which is derived from the LUID and therefore session-scoped; the
    durable identity is the PCI BDF (ADR-0042), so the sibling ``b70/events.jsonl`` is read
    to resolve adapter -> BDF and the result is keyed by BDF whenever that succeeds.

    ⚠ The returned dict carries ``burst_window`` -- the reference's OWN declared seconds and
    client count, read from the receipt, not asserted here -- and ``caveat``, because the
    denominator is a transient: see ``DUTY_REFERENCE_CAVEAT``. A caller that reports a duty
    fraction without one of them is reporting a numerator.
    """
    path = Path(receipt_path)
    doc = json.loads(path.read_text(encoding="utf-8-sig"))
    power = doc.get("power") or {}
    if "cards" not in power:
        raise ValueError("reference receipt %s carries no power.cards" % path)
    bdf_by_adapter: dict = {}
    events = path.parent / "b70" / "events.jsonl"
    if events.is_file():
        with events.open(encoding="utf-8-sig") as handle:
            for line in handle:
                line = line.strip()
                if not line or '"ai"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("k") == "ai" and row.get("bdf"):
                    bdf_by_adapter[row.get("a")] = row["bdf"]
    cards: dict = {}
    for adapter, card in (power.get("cards") or {}).items():
        burst = card.get("burst") or {}
        ambient = card.get("ambient") or {}
        bdf = bdf_by_adapter.get(adapter)
        cards[bdf or adapter] = {
            "adapter": adapter, "bdf": bdf, "desc": card.get("desc"),
            "burst_p50_w": burst.get("p50_w"), "burst_p95_w": burst.get("p95_w"),
            "ambient_p50_w": ambient.get("p50_w"),
        }
    burst = doc.get("burst") or {}
    intervals = [(card.get("burst") or {}).get("intervals")
                 for card in (power.get("cards") or {}).values()]
    intervals = [n for n in intervals if isinstance(n, (int, float))]
    return {"source": str(path), "counter": power.get("counter"),
            "keyed_by": "bdf" if bdf_by_adapter else "adapter",
            "frozen": "2026-09-09 compute control; the render half is not applicable "
                      "(lane paused under the Hatchet cutover; compute 0.0% during encode)",
            "burst_window": {"declared_s": burst.get("seconds"),
                             "clients": burst.get("clients"),
                             "prompt_tokens": burst.get("prompt_tokens"),
                             "requests": burst.get("requests"),
                             "measured_intervals_s": max(intervals) if intervals else None},
            "caveat": DUTY_REFERENCE_CAVEAT,
            "cards": cards}


def duty_cycle(cards: dict, reference: dict, window_ns: tuple | None = None,
               threshold_frac: float = DUTY_THRESHOLD_FRAC) -> dict:
    """Fraction of WALL TIME each card's dJ/dt is above ``threshold_frac`` x its OWN ref p50.

    Time-weighted, per card, keyed by BDF. Intervals are counted only when they lie wholly
    inside ``window_ns``; one straddling an edge is counted in neither and reported. A card
    with no reference entry, or a reference with no burst p50, yields ``None`` -- never a
    number derived from the other card's reference.

    ⚠ The result carries ``reference_caveat`` and ``reference_burst_window`` beside the
    fraction, and they are NOT decoration: the denominator is a ~92 s burst standing in for
    workloads that run hours (``DUTY_REFERENCE_CAVEAT``). Anything that renders duty -- a
    receipt, the reducer, a card, an article -- carries them with it or reports a numerator.
    """
    ref_cards = (reference or {}).get("cards") or {}
    out: dict = {}
    for adapter, card in (cards or {}).items():
        key = card.get("bdf") or adapter
        ref = ref_cards.get(key) or ref_cards.get(adapter)
        ref_p50 = (ref or {}).get("burst_p50_w")
        entry = {"adapter": adapter, "bdf": card.get("bdf"), "desc": card.get("desc"),
                 "reference_p50_w": ref_p50,
                 "threshold_w": round(threshold_frac * ref_p50, 2) if ref_p50 else None,
                 "threshold_frac": threshold_frac,
                 "intervals_considered": 0, "intervals_above": 0, "straddling_intervals": 0,
                 "seconds_considered": 0.0, "seconds_above": 0.0, "duty_cycle": None,
                 "reason": None}
        if not ref_p50:
            entry["reason"] = "no frozen reference burst p50 for this card"
            out[key] = entry
            continue
        threshold = threshold_frac * ref_p50
        considered_s = above_s = 0.0
        for t0, t1, watts in card.get("intervals") or ():
            if window_ns is not None:
                lo, hi = window_ns
                if t1 <= lo or t0 >= hi:
                    continue
                if t0 < lo or t1 > hi:
                    entry["straddling_intervals"] += 1
                    continue
            span = (t1 - t0) / NS
            considered_s += span
            entry["intervals_considered"] += 1
            if watts > threshold:
                above_s += span
                entry["intervals_above"] += 1
        entry["seconds_considered"] = round(considered_s, 3)
        entry["seconds_above"] = round(above_s, 3)
        entry["duty_cycle"] = round(above_s / considered_s, 4) if considered_s > 0 else None
        if considered_s <= 0:
            entry["reason"] = "no intervals inside the cell window"
        out[key] = entry
    return {"threshold_frac": threshold_frac, "window_ns": list(window_ns) if window_ns else None,
            "reference_source": (reference or {}).get("source"), "cards": out,
            "definition": "fraction of cell wall time this card's dJ/dt exceeded "
                          "%.2fx its own frozen reference burst p50" % threshold_frac,
            "reference_burst_window": (reference or {}).get("burst_window"),
            "reference_caveat": (reference or {}).get("caveat") or DUTY_REFERENCE_CAVEAT}


# ----------------------------------------------------- b70tools stream -> gate 8: thermal --
def _temp_percentile(ordered: list, q: float):
    """Nearest-rank, the convention the harness uses for its own p50/p95/p99.

    Deliberately NOT the interpolating median ``sat_reference_capture._stats`` uses for
    watts. These counters are emitted ON CHANGE, so a cell can carry two or three readings;
    nearest-rank never invents a temperature between two readings and never returns a p95
    BELOW the p50, which an ``int(0.95 * (n - 1))`` index does at n = 2.
    """
    if not ordered:
        return None
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _temp_stats(values: list) -> tuple:
    """(p50, p95) over readings, nearest-rank. ``(None, None)`` for no readings."""
    if not values:
        return None, None
    ordered = sorted(values)
    return (round(_temp_percentile(ordered, 0.50), 1),
            round(_temp_percentile(ordered, 0.95), 1))


def thermal_summary(events_path: Path, window_ns: tuple | None = None) -> dict:
    """Per-B70, per-counter temperatures from the stream every cell already writes.

    Same parse and same timebase as ``card_intervals`` / ``reduce_stream``: ``t`` is
    NANOSECONDS on the boot-relative perf_counter clock, cards are keyed by the b70tools
    ADAPTER id (session-scoped, derived from the LUID) and each carries the durable PCI BDF
    beside it (ADR-0042) so the report can name the card.

    Per counter, per card:
      ``idle_c``      the COOLEST reading in the capture's leading quarter, taken before the
                      load window -- the ambient baseline this room and this season give the
                      board, not a property of the board;
      ``busy_p50_c`` / ``busy_p95_c``  over the samples inside ``window_ns`` (over the whole
                      capture, said so in ``reason``, when no window is given);
      ``max_c``       the hottest reading ANYWHERE in the capture. The abort term must not be
                      able to miss a spike that lands just outside the load window;
      ``delta_c``     ``busy_p95_c - idle_c`` -- the workload-attributable rise;
      ``samples``     how many readings there were at all.

    CADENCE, measured 2026-09-09 and stated because it changes what these numbers mean:
    this b70tools build emits the temperature counters ON CHANGE, not per tick. A 260 s
    capture carried 7-13 readings per counter per card, irregularly spaced. So the
    percentiles are nearest-rank over READINGS, NOT time-weighted, and a leading quarter can
    legitimately hold one sample or none -- which is recorded as a reason, never as a zero.

    An absent counter is a LOUD NULL with a stated reason, never an implied "cool".
    """
    ident: dict = {}
    samples: dict = {}
    present: set = set()
    cap_lo = cap_hi = None
    with Path(events_path).open(encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("k") == "ai":
                ident[row.get("a")] = {"desc": row.get("desc") or "", "bdf": row.get("bdf")}
                continue
            if row.get("k") != "ms":
                continue
            try:
                t = int(row.get("t") or 0)
            except (TypeError, ValueError):
                continue
            cap_lo = t if cap_lo is None else min(cap_lo, t)
            cap_hi = t if cap_hi is None else max(cap_hi, t)
            name = row.get("n")
            if name in THERMAL_COUNTERS:
                present.add(name)
                try:
                    samples.setdefault((row.get("a"), name), []).append((t, float(row["v"])))
                except (KeyError, TypeError, ValueError):
                    continue

    idle_hi = None
    if cap_lo is not None and cap_hi is not None:
        idle_hi = cap_lo + int((cap_hi - cap_lo) * THERMAL_IDLE_FRACTION)
        if window_ns is not None:
            idle_hi = min(idle_hi, int(window_ns[0]))

    cards: dict = {}
    for adapter, info in ident.items():
        if "B70" not in (info.get("desc") or ""):
            continue  # the iGPU is not part of the board's thermal picture
        counters: dict = {}
        for name in THERMAL_COUNTERS:
            rows = sorted(samples.get((adapter, name)) or ())
            entry = {"idle_c": None, "busy_p50_c": None, "busy_p95_c": None, "max_c": None,
                     "delta_c": None, "samples": len(rows), "idle_samples": 0,
                     "busy_samples": 0, "reason": None}
            if not rows:
                entry["reason"] = ("stream carries no %r" % name if name not in present
                                   else "no %r samples for this adapter" % name)
                counters[name] = entry
                continue
            reasons: list = []
            entry["max_c"] = round(max(v for _t, v in rows), 1)
            idle = [v for t, v in rows if idle_hi is not None and t <= idle_hi]
            entry["idle_samples"] = len(idle)
            if idle:
                entry["idle_c"] = round(min(idle), 1)
            else:
                reasons.append("no reading in the capture's leading quarter before the load "
                               "window, so there is no idle baseline: this build emits "
                               "temperatures ON CHANGE, not per tick")
            if window_ns is None:
                busy = [v for _t, v in rows]
                reasons.append("no load window given: the busy figures cover the whole capture")
            else:
                lo, hi = int(window_ns[0]), int(window_ns[1])
                busy = [v for t, v in rows if lo <= t <= hi]
                if not busy:
                    reasons.append("no %r sample inside the load window" % name)
            entry["busy_samples"] = len(busy)
            entry["busy_p50_c"], entry["busy_p95_c"] = _temp_stats(busy)
            if entry["busy_p95_c"] is not None and entry["idle_c"] is not None:
                entry["delta_c"] = round(entry["busy_p95_c"] - entry["idle_c"], 1)
            entry["reason"] = "; ".join(reasons) or None
            counters[name] = entry
        cards[adapter] = {"adapter": adapter, "bdf": info.get("bdf"), "desc": info.get("desc"),
                          "counters": counters}
    return {
        "counters": list(THERMAL_COUNTERS),
        "counters_present": sorted(present),
        "capture_ns": [cap_lo, cap_hi] if cap_lo is not None else None,
        "window_ns": [int(window_ns[0]), int(window_ns[1])] if window_ns else None,
        "idle_window_ns": [cap_lo, idle_hi] if idle_hi is not None else None,
        "idle_fraction": THERMAL_IDLE_FRACTION,
        "timebase": "t is ns on the perf_counter clock",
        "cards": cards,
        "cadence": "this b70tools build emits the temperature counters ON CHANGE, not per "
                   "tick: percentiles are over readings, not time-weighted",
        "definition": "idle = coolest reading in the capture's leading %d%% before the load "
                      "window; busy = readings inside the load window; max = hottest reading "
                      "anywhere in the capture; delta = busy p95 - idle"
                      % round(THERMAL_IDLE_FRACTION * 100),
    }


_THERMAL_RANK = {"null": 0, "pass": 1, "warn": 2, "fail": 3}


def _worst(outcomes: list) -> str:
    return max(outcomes or ["null"], key=lambda o: _THERMAL_RANK.get(o, 0))


class LiveThermalGuard:
    r"""Poll the growing b70tools stream WHILE the load runs, and stop the cell on a breach.

    Gate 8 is the other half of this and it is not a substitute: it parses a stream that has
    already self-terminated, so it can mark a cell that reached 95 C but it cannot prevent one.
    The 2026-08-27 replica abort was caught by a live 10-second poll that no longer exists, and
    the shape that produced it climbed 78 -> 96 C in FORTY-EIGHT SECONDS. A gate that reads the
    corpse is not protection for that.

    ⚠ BLIND IS TOLERATED FOR A GRACE PERIOD HERE, unlike the standalone watchdog. A cell's stream
    is launched moments before the load, so the first polls legitimately see no adapters and no
    counters yet. Aborting on that would kill every cell in its first seconds. After ``grace_s``
    the tolerance ends, because running the replica shape with no thermal visibility is precisely
    what this exists to prevent.

    ⚠ A QUIET TEMPERATURE IS NOT BLINDNESS -- liveness is the stream growing, not the recency of a
    reading. See ``thermal_watchdog.STREAM_DEAD_AFTER_S``; the counters emit ON CHANGE and a stable
    card correctly reports nothing for minutes at a time.

    The thread only ever OBSERVES. Stopping the load is the caller's, by the pid it launched --
    never by image name, which on this box has taken down production services.
    """

    def __init__(self, events_path, poll_s: float = 5.0, grace_s: float = 90.0) -> None:
        self.events_path = Path(events_path)
        self.poll_s = poll_s
        self.grace_s = grace_s
        self.breach: dict | None = None
        self.samples: list = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_ns: int | None = None

    def _run(self) -> None:
        import thermal_watchdog as watchdog  # local: the runner must import cleanly without it
        state = watchdog.ThermalState()
        while not self._stop.is_set():
            try:
                state.poll(self.events_path)
                verdict = watchdog.evaluate(state)
            except Exception as exc:  # noqa: BLE001 - instrumentation must not crash a cell
                self.samples.append({"verdict": "error", "reason": str(exc)})
                self._stop.wait(self.poll_s)
                continue
            elapsed = (time.perf_counter_ns() - (self._started_ns or 0)) / 1_000_000_000
            self.samples.append({"at_s": round(elapsed, 1), "verdict": verdict["verdict"],
                                 "reason": verdict["reason"], "hottest": verdict["hottest"]})
            if verdict["verdict"] in ("abort_absolute", "abort_slope"):
                self.breach = {**verdict, "at_s": round(elapsed, 1)}
                return
            if verdict["verdict"] == "blind" and elapsed > self.grace_s:
                self.breach = {**verdict, "at_s": round(elapsed, 1),
                               "reason": "%s (and the %.0fs grace period has passed -- a cell "
                                         "cannot run unseen)" % (verdict["reason"], self.grace_s)}
                return
            self._stop.wait(self.poll_s)

    def start(self) -> None:
        self._started_ns = time.perf_counter_ns()
        self._thread = threading.Thread(target=self._run, name="live-thermal", daemon=True)
        self._thread.start()

    def stop(self) -> dict:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.poll_s + 5)
        verdicts = [s["verdict"] for s in self.samples]
        return {
            "polls": len(self.samples),
            "poll_s": self.poll_s,
            "grace_s": self.grace_s,
            "stopped_the_cell": self.breach is not None,
            "breach": self.breach,
            "hottest_seen": max((s.get("hottest") for s in self.samples
                                 if s.get("hottest")), key=lambda h: h["c"], default=None),
            "verdict_counts": {v: verdicts.count(v) for v in sorted(set(verdicts))},
            "samples": self.samples[-40:],
            "note": ("live poll during the load; gate 8 scores the finished stream separately. "
                     "blind is tolerated for the grace period because the stream starts moments "
                     "before the load."),
        }


def score_thermal(summary: dict, thresholds: dict | None = None) -> dict:
    """Score ``thermal_summary`` against the module constants. Gate 8's whole verdict.

    Returns the summary WITH the thresholds it was scored against and an outcome on every
    counter, every card and the board -- ``pass`` / ``warn`` / ``fail`` / ``null``. The
    absolute term is ``max_c`` (a spike that lands outside the load window is still a spike);
    the delta term warns only. ``exceeded`` is the top-level flag the receipt carries.

    A ``fail`` does NOT drop the cell. Like ``over_admitted`` the row is kept and excluded
    from the surface, because a cell that ran hot is real data about a real cell.
    """
    thresholds = thermal_thresholds() if thresholds is None else thresholds
    doc = copy.deepcopy(summary or {})
    doc["thresholds"] = thresholds
    delta_warn = thresholds.get("delta_warn_c", DELTA_WARN_C)
    hottest = None
    card_outcomes = []
    for card in (doc.get("cards") or {}).values():
        counter_outcomes = []
        for name, entry in (card.get("counters") or {}).items():
            limits = thresholds.get(name) or {}
            warn_c, abort_c = limits.get("warn_c"), limits.get("abort_c")
            entry["warn_c"], entry["abort_c"] = warn_c, abort_c
            max_c, delta_c = entry.get("max_c"), entry.get("delta_c")
            note = None
            if max_c is None or abort_c is None:
                entry["outcome"] = "null"
            elif max_c >= abort_c:
                entry["outcome"] = "fail"
                note = ("%.1f C at or above the %.0f C abort limit; %s"
                        % (max_c, abort_c, thresholds.get("attribution")))
            elif warn_c is not None and max_c >= warn_c:
                entry["outcome"] = "warn"
                note = "%.1f C at or above the %.0f C warn line" % (max_c, warn_c)
            elif delta_c is not None and delta_c >= delta_warn:
                entry["outcome"] = "warn"
                note = ("rise of %.1f C at or above the %.0f C delta warn line -- the absolute "
                        "is fine, the WORKLOAD is heating the card" % (delta_c, delta_warn))
            else:
                entry["outcome"] = "pass"
            # The measurement caveat (a missing idle baseline, an empty window) is kept and
            # the score's own reason is appended to it -- neither overwrites the other.
            entry["reason"] = "; ".join(x for x in (entry.get("reason"), note) if x) or None
            counter_outcomes.append(entry["outcome"])
            if max_c is not None and (hottest is None or max_c > hottest["max_c"]):
                hottest = {"card": card.get("bdf") or card.get("adapter"),
                           "adapter": card.get("adapter"), "counter": name,
                           "label": THERMAL_LABELS.get(name, name), "max_c": max_c,
                           "busy_p95_c": entry.get("busy_p95_c"), "idle_c": entry.get("idle_c"),
                           "delta_c": delta_c, "outcome": entry["outcome"]}
        card["outcome"] = _worst(counter_outcomes)
        card_outcomes.append(card["outcome"])
    outcome = _worst(card_outcomes)
    doc["outcome"] = outcome
    doc["hottest"] = hottest
    doc["exceeded"] = None if outcome == "null" else (outcome == "fail")
    doc["detail"] = _thermal_detail(doc, thresholds)
    return doc


def _thermal_detail(doc: dict, thresholds: dict) -> str:
    """The gate's sentence: what was measured, what the limit is, and WHOSE call it is."""
    hottest = doc.get("hottest")
    if not hottest:
        absent = ", ".join(n for n in THERMAL_COUNTERS
                           if n not in (doc.get("counters_present") or ()))
        return ("no thermal reading in this cell's stream (%s absent); abort %.0f C (%s) was "
                "never evaluated. %s"
                % (absent or "counters absent", VRAM_ABORT_C, thresholds.get("attribution"),
                   thresholds.get("ambient")))
    return ("%s p95 %s C (idle %s, delta %s, max %s) on %s; abort %.0f C, warn %.0f C, delta "
            "warn %.0f C. %s. %s"
            % (hottest["label"], hottest.get("busy_p95_c"), hottest.get("idle_c"),
               hottest.get("delta_c"), hottest.get("max_c"), hottest.get("card"),
               (thresholds.get(hottest["counter"]) or {}).get("abort_c") or VRAM_ABORT_C,
               (thresholds.get(hottest["counter"]) or {}).get("warn_c") or VRAM_WARN_C,
               thresholds.get("delta_warn_c", DELTA_WARN_C),
               thresholds.get("attribution"), thresholds.get("ambient")))


# ------------------------------------------------------------------------ admission --
BUDGET = "vram.local.budget_bytes"                          # adapter-wide DXGI budget
COMMITTED = "gpu.adapter.vram.local.bytes_committed"        # adapter-wide, ALL processes
AVAILABLE = "vram.local.available_for_reservation_bytes"    # adapter-wide free, independent cross-check
PROCESS_USAGE = "vram.local.current_usage_bytes"            # b70tools' OWN usage -- never the headroom term
USAGE = PROCESS_USAGE                                       # kept for callers; see the trap below
NON_LOCAL_USAGE = "vram.non_local.current_usage_bytes"
_GB = 1024.0 ** 3


def budget_headroom(events_path: Path) -> dict:
    """Adapter-wide VRAM headroom per card: DXGI budget - bytes committed by ALL processes.

    The trap this replaces (first live cell, 2026-09-09): DXGI ``QueryVideoMemoryInfo``'s
    ``CurrentUsage`` is PER-PROCESS. Read through b70tools it reports b70tools' own footprint
    -- 4,096 bytes on a card holding 16 GB of production weights -- so budget minus that
    "usage" was ~31 GB on every cell and the gate could never fire. That is the A12 idle
    counter in a new coat: a green gate proving nothing. ``gpu.adapter.vram.local.bytes_committed``
    is adapter-wide (it agrees with ff_cell's per-BDF placement evidence to the 0.1 GB) and
    ``available_for_reservation`` is an independent adapter-wide free figure recorded beside it.

    Cadence: this b70tools build samples these DXGI series ONCE PER CAPTURE, not per tick, so
    ``samples`` is typically 1 and the headroom is a bracket over the cell, not continuous
    coverage. ``over_admitted`` is True when any card's headroom goes negative, ``None`` when
    the stream never carried the pair -- unknown is recorded as unknown, and the row is kept
    either way (the card, gate 3).
    """
    ident: dict = {}
    series: dict = {}
    with Path(events_path).open(encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("k") == "ai":
                ident[row.get("a")] = {"desc": row.get("desc") or "", "bdf": row.get("bdf")}
            elif row.get("k") == "ms" and row.get("n") in (
                    BUDGET, COMMITTED, AVAILABLE, PROCESS_USAGE, NON_LOCAL_USAGE):
                series.setdefault(row.get("a"), []).append(
                    (int(row.get("t") or 0), row["n"], float(row["v"])))
    cards: dict = {}
    for adapter, rows in series.items():
        info = ident.get(adapter) or {}
        if "B70" not in (info.get("desc") or ""):
            continue
        rows.sort()
        budget = committed = available = process_usage = non_local = None
        headrooms: list[float] = []
        for _t, name, value in rows:
            if name == BUDGET:
                budget = value
            elif name == COMMITTED:
                committed = value
            elif name == AVAILABLE:
                available = value
            elif name == PROCESS_USAGE:
                process_usage = value
            else:
                non_local = value
            if budget is not None and committed is not None:
                headrooms.append(budget - committed)
        key = info.get("bdf") or adapter
        cards[key] = {
            "adapter": adapter, "bdf": info.get("bdf"), "desc": info.get("desc"),
            "budget_gb": round(budget / _GB, 3) if budget is not None else None,
            "committed_gb": round(committed / _GB, 3) if committed is not None else None,
            "available_for_reservation_gb": round(available / _GB, 3) if available is not None else None,
            "process_usage_gb": round(process_usage / _GB, 3) if process_usage is not None else None,
            "non_local_usage_gb": round(non_local / _GB, 3) if non_local is not None else None,
            "samples": len(headrooms),
            "min_headroom_gb": round(min(headrooms) / _GB, 3) if headrooms else None,
            "last_headroom_gb": round(headrooms[-1] / _GB, 3) if headrooms else None,
        }
    negatives = [c["min_headroom_gb"] for c in cards.values() if c["min_headroom_gb"] is not None]
    over = None if not negatives else any(v < 0 for v in negatives)
    return {"cards": cards, "over_admitted": over,
            "headroom_term": "%s - %s (adapter-wide); %s recorded as a cross-check; %s is b70tools' "
                             "own per-process usage and is never the headroom term"
                             % (BUDGET, COMMITTED, AVAILABLE, PROCESS_USAGE),
            "note": "sampled ONCE PER CAPTURE by this b70tools build (not per tick): the headroom "
                    "is a bracket over the cell, not continuous coverage. An over_admitted row is "
                    "KEPT and excluded from the surface, not dropped."}


# --------------------------------------------------------------------- depth-0 (ETW) --
_ARM = re.compile(r"^ARM\s+(\S+)")
_QUEUE = re.compile(r"^\s*queue\s+(\S+)\s")
_UNION = re.compile(r"^\s*UNION of \d+ deep compute queues")
_DEPTH0 = re.compile(r"depth0=([0-9.]+)%")


def parse_etw4_depth(stdout: str) -> dict:
    """``f0`` per queue from etw4_depth.py's printed report (its only interface).

    The queue line's OCCUPANCY row carries the corrected depth0 first and the raw-window
    one in parentheses; the FIRST match is the corrected figure. A line that does not parse
    yields nothing rather than a guess -- an unparsed report is ``null``, never a number.
    """
    arms: dict = {}
    arm = None
    queue = None
    for line in (stdout or "").splitlines():
        m = _ARM.match(line.strip())
        if m:
            arm = m.group(1)
            arms.setdefault(arm, {})
            queue = None
            continue
        if _UNION.match(line):
            queue = "UNION-deep-compute"
            continue
        m = _QUEUE.match(line)
        if m:
            queue = m.group(1)
            continue
        m = _DEPTH0.search(line)
        if m and arm is not None and queue is not None:
            try:
                arms[arm][queue] = round(float(m.group(1)) / 100.0, 6)
            except ValueError:
                pass
            queue = None if queue == "UNION-deep-compute" else queue
    return {"arms": arms,
            "f0_union": {a: q.get("UNION-deep-compute") for a, q in arms.items()} or None}


# -------------------------------------------------------------------------- receipt --
def blank_receipt() -> dict:
    """Every field present, every value ``null``. Nothing is ever absent from a receipt."""
    return {field: None for field in RECEIPT_FIELDS}


def assert_receipt_complete(row: dict) -> dict:
    missing = [f for f in RECEIPT_FIELDS if f not in row]
    extra = [f for f in row if f not in RECEIPT_FIELDS]
    if missing or extra:
        raise ValueError("receipt schema drift -- missing %s, unexpected %s" % (missing, extra))
    return row


def gate_outcomes(row: dict) -> list:
    """All eight gates, always all eight, each with an outcome and its reason."""
    out = []
    for number, name in GATES:
        outcome, detail = "null", None
        if name == "warm_then_measure":
            warm = row.get("warm") or {}
            if not warm:
                outcome, detail = "null", "not run"
            elif warm.get("flat"):
                outcome = "pass"
                detail = ("flat at %.2f%% after %d iteration(s); unwarmed rep-1 %s tok/s"
                          % (warm.get("final_spread_pct") or 0.0, warm.get("iterations_used") or 0,
                             warm.get("unwarmed_rep1_tok_s")))
            else:
                outcome = "fail"
                detail = ("never settled within %.1f%% in %d iterations (%s)"
                          % (warm.get("threshold_pct") or FLATNESS_SPREAD_PCT,
                             warm.get("iterations_used") or 0, warm.get("error")))
        elif name == "production_guard":
            before = row.get("guard_before") or {}
            verdict = before.get("verdict")
            if verdict is None:
                outcome, detail = "null", "guard not sampled"
            elif verdict == "at_rate":
                outcome, detail = "pass", "at_rate at cell start"
            elif verdict in ("degraded", "stalled", "unreachable"):
                outcome, detail = "fail", "%s -- stop condition" % verdict
            else:
                outcome, detail = "unknown", "%s is reported, never passed" % verdict
        elif name == "admission":
            head = row.get("budget_headroom") or {}
            over = head.get("over_admitted")
            if over is None:
                outcome, detail = "null", "no budget/usage pair in the stream"
            elif over:
                outcome, detail = "over_admitted", "budget headroom went negative; row kept, " \
                                                  "excluded from the surface"
            else:
                outcome, detail = "pass", "budget headroom stayed non-negative"
        elif name == "in_flight":
            poll = row.get("slots_poll") or {}
            window = row.get("slots_poll_load_window") or {}
            span_detail = ("span %s polls, all-slots-busy %s (%s)"
                           % (poll.get("ok_polls"), poll.get("all_busy_fraction"),
                              SPAN_VS_WINDOW_NOTE))
            if not poll.get("ok_polls"):
                outcome, detail = "null", "no successful /slots poll"
            elif not window.get("ok_polls"):
                outcome = "null"
                detail = ("no /slots poll inside the load window (%s); %s"
                          % (window.get("reason") or "window underivable", span_detail))
            else:
                # P5 is scored on the LOAD, so the load window is the term; the span figure
                # is reported beside it and never used as the outcome.
                outcome = "pass"
                detail = ("load window %s polls over %ss, all-slots-busy %s, "
                          "busy-slot fraction %s; %s; busy-slots/decode delta %s"
                          % (window.get("ok_polls"), window.get("window_s"),
                             window.get("all_busy_fraction"), window.get("busy_slot_fraction"),
                             span_detail, row.get("n_busy_slots_per_decode_delta")))
        elif name == "board_duty_cycle":
            duty = ((row.get("duty_cycle") or {}).get("cards") or {})
            values = [c.get("duty_cycle") for c in duty.values() if c.get("duty_cycle") is not None]
            ratio = ((row.get("symmetry") or {}) or {}).get("ratio")
            if not values:
                outcome, detail = "null", "no duty cycle derivable for this cell"
            elif ratio is not None and ratio < SYMMETRY_PARTIAL_BELOW:
                outcome = "partially_scored"
                detail = ("symmetry ratio %.3f < %.1f -- the quieter card's power is weaker "
                          "evidence; per-card duty %s" % (ratio, SYMMETRY_PARTIAL_BELOW, values))
            else:
                outcome, detail = "pass", "per-card duty %s (symmetry %s)" % (values, ratio)
        elif name == "depth0_fraction":
            depth0 = row.get("depth0") or {}
            if depth0.get("f0") is None:
                outcome = "null"
                detail = depth0.get("reason") or "no capture; recorded, not dropped"
            else:
                outcome, detail = "pass", "f0 %s" % (depth0.get("f0"),)
        elif name == "prefill_real":
            prefill = row.get("prefill_real") or {}
            outcome = prefill.get("outcome") or "null"
            detail = prefill.get("reason") or "no /metrics bracket; prefill unknown"
            if prefill.get("fraction") is not None:
                detail = ("%s [uncached %s / cached %s / expected %s, fraction %s]"
                          % (detail, prefill.get("uncached"), prefill.get("cached"),
                             prefill.get("expected"), prefill.get("fraction")))
        elif name == "thermal":
            thermal = row.get("thermal") or {}
            outcome = thermal.get("outcome") or "null"
            detail = thermal.get("detail")
            if not detail:
                detail = ("no thermal reduction for this cell; abort %.0f C (%s) was never "
                          "evaluated. %s"
                          % (VRAM_ABORT_C, THERMAL_ATTRIBUTION, THERMAL_AMBIENT_NOTE))
            if outcome == "fail":
                detail = "%s -- row KEPT and excluded from the surface, never dropped" % detail
        out.append({"gate": number, "name": name, "outcome": outcome, "detail": detail})
    return out


# --------------------------------------------------------------- command construction --
def load_argv(python: str, cell: str, depth: int, n: int, run_id: str | None = None) -> list:
    """The card's load-generator invocation, verbatim, with this cell's knobs.

    ``--no-cache-prompt`` on EVERY cell (gate 7). The harness builds a byte-identical prompt
    per request, so without it llama-server's prompt cache serves the prefix and the
    surface's size axis measures nothing: on np2-p512-c2-r4 the server processed 64 prompt
    tokens and served 3,073 from cache for 6 x 440 expected.
    """
    return [python, str(QWEN38), "load",
            "--run-id", run_id or ("sat-l1-" + cell),
            "--endpoint", "http://127.0.0.1:%d" % PRODUCTION_PORT,
            "--candidate", CANDIDATE, "--topology", TOPOLOGY, "--model", MODEL_ALIAS,
            "--concurrency", str(n), "--prompt-tokens", str(depth),
            "--max-tokens", str(MAX_TOKENS),
            "--requests-per-client", str(REQUESTS_PER_CLIENT),
            "--seed", str(SEED), "--disable-thinking", "--no-cache-prompt"]


def ff_cell_argv(python: str, cell: str, reps: int, load_cmd: str, json_out: Path,
                 restart: bool = False) -> list:
    argv = [python, str(HERE / "ff_cell.py"), "--cell", cell, "--expect", "both-b70",
            "--rung", "omen-arc", "--reps", str(reps),
            "--no-ledger", "--json-out", str(json_out), "--command", load_cmd]
    if restart:
        argv.insert(argv.index("--command"), "--restart")
    return argv


def b70_stream_argv(out_dir: Path, ticks: int) -> list:
    return [str(B70TOOLS), "--run", "--ticks", str(ticks), "--cadence-ms", "1000",
            "--flush-every-tick", "--out", str(out_dir)]


def estimated_cell_seconds(depth: int, n: int, np_slots: int) -> int:
    """A generous upper bound so the self-terminating stream outlives the cell.

    Not a prediction of the cell: the b70tools stream has no stop verb (it exits after
    ``--ticks``), so it must be sized long. Deliberately coarse and always overridable with
    ``--cell-seconds``; the receipt records whether the stream actually covered the window.
    """
    per_request = {512: 6, 8192: 30, 32768: 260}.get(depth, 30)
    waves = max(1, -(-n // max(1, np_slots)))
    return int(REQUESTS_PER_CLIENT * per_request * waves) + 60


# ------------------------------------------------------------------------- the plan --
def plan_cell(cell: str, python: str, args) -> dict:
    """Everything a cell will do, as data. This is what ``--dry-run`` prints."""
    parts = parse_cell_id(cell)
    depth, n, np_slots, rep = parts["depth"], parts["n"], parts["np"], parts["rep"]
    reps = args.reps
    cell_dir = Path(args.out) / cell
    info = bearer_info()
    cell_s = args.cell_seconds or estimated_cell_seconds(depth, n, np_slots)
    ticks = args.stream_ticks or (cell_s + 180)
    warm_id = "sat-l1-%s-warmdiscard" % cell
    load_cmd = subprocess.list2cmdline(load_argv(python, cell, depth, n))
    return {
        "cell": cell, "phase": 1 if np_slots == CARD_NP else 2,
        "regime": {"model": MODEL_ALIAS, "quant": "Q4_K_M", "depth_tokens": depth,
                   "concurrency": n, "np": np_slots, "ub": UB, "ctx": TOTAL_CTX,
                   "per_slot_ctx": per_slot_ctx(np_slots), "repeat": rep,
                   "admissible": admissible(depth, np_slots),
                   "max_tokens": MAX_TOKENS, "requests_per_client": REQUESTS_PER_CLIENT,
                   "seed": SEED, "topology": TOPOLOGY, "candidate": CANDIDATE,
                   "placement": None, "epoch": None,
                   "endpoint": "http://127.0.0.1:%d" % PRODUCTION_PORT},
        "repeats_planned": repeats_for(np_slots, depth, n),
        "reps_ratecheck": reps,
        "cell_dir": str(cell_dir),
        "bearer": info,
        "estimated_cell_seconds": cell_s,
        "stream_ticks": ticks,
        "depth0_cell": is_depth0_cell(n, rep),
        "steps": [
            {"step": 1, "what": "guard gate at cell start (must read at_rate)",
             "how": "hearth.health.guard.gate('cell start') -- passive rungstate read + "
                    "unauthenticated GET http://127.0.0.1:%d/health" % PRODUCTION_PORT},
            {"step": 2, "what": "warm to flatness (ADR-0043)",
             "how": "ff_ratecheck.measure(rung='omen-arc', reps=%d) until repeat_spread_pct "
                    "<= %.1f%%, max %d iterations; every iteration recorded, the first "
                    "iteration's first rep is the unwarmed rep-1"
                    % (FLATNESS_REPS, FLATNESS_SPREAD_PCT, FLATNESS_MAX_ITERATIONS)},
            {"step": 3, "what": "one DISCARDED depth-matched load (N=2, this block's depth)",
             "argv": load_argv(python, cell, depth, 2, run_id=warm_id),
             "env": redacted_bearer(info)},
            {"step": 4, "what": "start the b70tools 1 Hz stream (self-terminating)",
             "argv": b70_stream_argv(cell_dir / "b70", ticks),
             "note": "t is NANOSECONDS on the perf_counter clock; wall<->ns mapping is "
                     "taken in-process at launch"},
            {"step": 5, "what": "scrape /metrics (before)",
             "how": "GET http://127.0.0.1:%d/metrics with %s"
                    % (PRODUCTION_PORT, redacted_bearer(info))},
            {"step": 6, "what": "poll /slots at 1 s for the duration of the cell",
             "how": "GET http://127.0.0.1:%d/slots with %s"
                    % (PRODUCTION_PORT, redacted_bearer(info))},
            {"step": 7, "what": "run the cell through the ff_cell invariant",
             "argv": ff_cell_argv(python, cell, reps, load_cmd, cell_dir / "ff-cell-row.json"),
             "env": redacted_bearer(info),
             "note": "ff_cell does epoch -> pre-rate -> b70 sample INSIDE that window (A12) "
                     "-> health/placement gate -> ONE cell -> post-rate -> the nine "
                     "provenance fields"},
            {"step": 8, "what": "scrape /metrics (after) and stop the /slots poller",
             "how": "busy-slots/decode delta = after - before"},
            {"step": 9, "what": "per-round launch skew (INSTRUMENT -- no gate reads it; "
                                "gate 8 is thermal and reads only the temperature counters)",
             "how": "read the last %d MiB of %s and time this cell's rounds against each "
                    "other; a round whose two launches are > %.0f ms apart is RECORDED, "
                    "never failed. Anchor: log mtime - newest slot-event uptime, then the "
                    "best whole-cell fit; unanchorable -> launch_skew is a loud null"
                    % (SERVE_LOG_TAIL_BYTES // (1024 * 1024), SERVE_LOG,
                       LAUNCH_SKEW_THRESHOLD_MS)},
            {"step": 10, "what": "wait for the b70tools stream to exit, then reduce",
             "how": "sat_reference_capture.reduce_stream(events, 'gpu', window_ns) + "
                    "card_intervals(events) for the time-weighted duty cycle + "
                    "thermal_summary(events, window_ns) for gate 8"},
            {"step": 11, "what": "symmetry gate",
             "argv": [python, str(VERDICT_PY), str(cell_dir / "b70" / "events.jsonl"), "--json"],
             "note": "ratio < %.1f -> the row is partially_scored" % SYMMETRY_PARTIAL_BELOW},
            {"step": 12, "what": "duty cycle against the FROZEN reference",
             "how": "per card, fraction of cell wall time dJ/dt > %.2f x its own reference "
                    "burst p50, read from %s" % (DUTY_THRESHOLD_FRAC, REFERENCE_RECEIPT)},
            {"step": 13, "what": "depth-0 (ETW4)",
             "how": _depth0_plan(cell, parts, python, cell_dir)},
            {"step": 14, "what": "guard.wait_for_fresh after the cell ENDED",
             "how": "a rung sample whose reading predates the cell is `unknown`, never a "
                    "pass; a sample taken during the cell is kept as rung_during"},
            {"step": 15, "what": "write ONE receipt and append ONE ledger row",
             "how": "%s + %s (probe=%s)" % (cell_dir / "receipt.json", LEDGER, PROBE)},
        ],
        "ledger": str(LEDGER),
        "receipt": str(cell_dir / "receipt.json"),
    }


def _depth0_plan(cell: str, parts: dict, python: str, cell_dir: Path) -> str:
    if not is_depth0_cell(parts["n"], parts["rep"]):
        return ("not a depth-0 cell (the card takes one capture per depth block at N=2 and "
                "N=16, first repeat only); depth0 fields are null")
    if not ETW_SESSION_MANIFEST.is_file():
        return ("REFUSED: no %s -- the ring is not running. The runner never starts or stops "
                "tracing (Derek runs etw6_session.ps1 -Start once for all of Lap 1); depth0 "
                "fields are null, recorded not dropped" % ETW_SESSION_MANIFEST)
    return ("etw6_watch.snapshot(tag=%s) -> cap-<stamp>-%s.etl + .json; then %s %s --manifest "
            "<cap.json> --etl <cap.etl> --requests <this cell's REAL harness rows> --max-dump-mb "
            "<budget> --out %s; then %s %s <report.json> -> f0 per queue"
            % (cell, cell, python, ETW10, cell_dir / "etw" / "report.json", python, ETW4))


# ------------------------------------------------------------------- live execution --
def _http(url: str, token: str | None, timeout: float = 15.0) -> tuple:
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", "Bearer %s" % token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace") if exc.fp else ""
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


class SlotPoller:
    """1 Hz ``/slots`` poller. Every sample carries a wall stamp; errors are kept, not hidden."""

    def __init__(self, port: int, token: str | None, interval_s: float = 1.0):
        self.url = "http://127.0.0.1:%d/slots" % port
        self.token = token
        self.interval_s = interval_s
        self.samples: list = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            t = time.time()
            status, body = _http(self.url, self.token, timeout=5.0)
            if status == 200:
                try:
                    doc = json.loads(body)
                except ValueError:
                    self.samples.append({"t": t, "error": "non-JSON /slots body"})
                else:
                    slots = doc.get("slots") if isinstance(doc, dict) else doc
                    if isinstance(slots, list):
                        self.samples.append({"t": t, "slots": [
                            {"id": s.get("id"), "is_processing": bool(s.get("is_processing")),
                             "n_prompt_tokens": s.get("n_prompt_tokens"),
                             "next_token": (s.get("next_token") or {}).get("n_decoded")
                             if isinstance(s.get("next_token"), dict) else s.get("next_token")}
                            for s in slots if isinstance(s, dict)]})
                    else:
                        self.samples.append({"t": t, "error": "unexpected /slots shape"})
            else:
                self.samples.append({"t": t, "error": "HTTP %s: %s" % (status, body[:120])})
            self._stop.wait(self.interval_s)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> list:
        self._stop.set()
        self._thread.join(timeout=10.0)
        return self.samples


def _iso_epoch(value) -> float | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


def cell_window_ns(rows: list, launch_wall: float, launch_ns: int) -> tuple | None:
    """[min(started_at), max(completed_at)] over the cell's REAL harness rows, in stream ns.

    ``time.time()`` and ``time.perf_counter_ns()`` are taken together in this process at
    stream launch, so the conversion is exact for this run and needs no clock discipline
    between processes.
    """
    starts = [t for t in (_iso_epoch(r.get("started_at")) for r in rows or ()) if t]
    ends = [t for t in (_iso_epoch(r.get("completed_at")) for r in rows or ()) if t]
    if not starts or not ends:
        return None
    lo = launch_ns + int((min(starts) - launch_wall) * NS)
    hi = launch_ns + int((max(ends) - launch_wall) * NS)
    return (lo, hi) if hi > lo else None


def run_cell(cell: str, python: str, args) -> dict:
    """One live cell. Every unmeasured field stays ``None``; nothing is inferred."""
    from hearth.health import guard
    from hearth.rotation import telemetry

    plan = plan_cell(cell, python, args)
    parts = parse_cell_id(cell)
    depth, n, np_slots = parts["depth"], parts["n"], parts["np"]
    cell_dir = Path(plan["cell_dir"])
    cell_dir.mkdir(parents=True, exist_ok=True)
    (cell_dir / "b70").mkdir(exist_ok=True)
    token = _token()
    child_env = dict(os.environ)
    if token:
        child_env[HARNESS_KEY_ENV] = token

    row = blank_receipt()
    row.update({"schema_version": 1, "probe": PROBE, "cell": cell, "coresident": True,
                "phase": plan["phase"], "regime": copy.deepcopy(plan["regime"]),
                "bearer": plan["bearer"], "runner_commit": _git_head(),
                "receipt_path": str(cell_dir / "receipt.json"), "notes": [],
                "load_rows_path": None, "status": "running",
                # Gate 7: load_argv passes --no-cache-prompt on every cell, so the surface's
                # size axis measures prefill instead of the server's prompt cache.
                "cache_prompt": False})

    # --- gate 2: the production guard, before anything is measured -------------------
    try:
        row["guard_before"] = guard.gate("cell start", state_reader=_rung_reader())
    except guard.ProductionDegraded as exc:
        row["guard_before"] = {"verdict": "stop", "error": str(exc)}
        return _finish(row, cell_dir, args, "REFUSED_GUARD", str(exc))
    row["guard_verdict"] = (row["guard_before"] or {}).get("verdict")
    if row["guard_verdict"] != "at_rate":
        return _finish(row, cell_dir, args, "REFUSED_GUARD",
                       "guard read %r at cell start; the card requires at_rate"
                       % row["guard_verdict"])

    # --- gate 1: warm to flatness, then ONE discarded depth-matched load ------------
    baselines = json.loads(BASELINES.read_text(encoding="utf-8-sig"))
    rung = (baselines.get("rungs") or {}).get("omen-arc")
    if rung is None:
        return _finish(row, cell_dir, args, "REFUSED_NO_RUNG", "no omen-arc rung in %s" % BASELINES)
    row["warm"] = warm_to_flatness(lambda: ff_ratecheck.measure(rung, FLATNESS_REPS))
    row["unwarmed_rep1_tok_s"] = row["warm"].get("unwarmed_rep1_tok_s")
    if not row["warm"].get("flat"):
        # ADR-0043 AS AMENDED 2026-09-09 (by this campaign's own P7 result). The old text
        # here read "warm, do not restart" -- which is exactly wrong in the case that makes
        # this branch fire. A rung that will not settle is usually a COLLAPSED rung, and
        # warming a collapsed rung does not recover it: measured 33% -> 48% over 24 sustained
        # requests, each rep LOWER than the last, 81.8% spread, while one restart restored
        # 100% at 0.24%. Report the depth so the operator can tell the two apart, and name
        # the discriminator rather than prescribing a remedy from here.
        settled = row["warm"].get("final_decode_tok_s")
        base = rung.get("baseline_decode_tok_s") or rung.get("baseline_tok_s")
        frac = ("%.0f%% of baseline" % (100.0 * settled / base)
                if settled and base else "fraction unknown")
        return _finish(row, cell_dir, args, "REFUSED_NOT_WARM",
                       "the rung never settled within %.1f%% (%s). ADR-0043 amended: shallow decay "
                       "(>=~64%%) -> warm; a COLLAPSE (~33%%) does not warm back -- restart once, "
                       "then let the keep-alive hold it. The restart is the discriminator: cleared "
                       "by one => idle collapse; survives one => a different class, and restarting "
                       "again is wrong." % (FLATNESS_SPREAD_PCT, frac))
    warm_argv = load_argv(python, cell, depth, 2, run_id="sat-l1-%s-warmdiscard" % cell)
    warm_proc = subprocess.run(warm_argv, capture_output=True, text=True, errors="replace",
                               env=child_env)
    row["warm_discard_load"] = {"argv": warm_argv, "returncode": warm_proc.returncode,
                                "stdout_tail": (warm_proc.stdout or "")[-800:],
                                "note": "discarded by construction: deep blocks must warm their "
                                        "own KV paths, and one short request cannot"}

    # --- admission, before ----------------------------------------------------------
    row["commit_free_gb_before"] = telemetry.commit_free_gb()

    # --- gates 4 and 5: start the stream and the pollers, then run the cell ---------
    events = cell_dir / "b70" / "events.jsonl"
    stream_argv = b70_stream_argv(cell_dir / "b70", plan["stream_ticks"])
    launch_wall, launch_ns = time.time(), time.perf_counter_ns()
    stream = None
    if B70TOOLS.is_file():
        stream = subprocess.Popen(stream_argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        row["notes"].append("b70tools not found at %s -- power fields stay null" % B70TOOLS)
    row["b70_stream"] = {"argv": stream_argv, "launched": stream is not None,
                         "launch_wall": launch_wall, "launch_ns": launch_ns,
                         "ticks": plan["stream_ticks"], "events": str(events)}

    status, body = _http("http://127.0.0.1:%d/metrics" % PRODUCTION_PORT, token)
    row["metrics_before"] = parse_prometheus(body) if status == 200 else {"http_status": status}
    poller = SlotPoller(PRODUCTION_PORT, token)
    poller.start()

    load_cmd = subprocess.list2cmdline(load_argv(python, cell, depth, n))
    ff_json = cell_dir / "ff-cell-row.json"
    ff_argv = ff_cell_argv(python, cell, args.reps, load_cmd, ff_json)
    row["load_command"] = load_cmd

    # --- the LIVE thermal guard runs for exactly as long as the load does -------------------
    # Gate 8 below scores the finished stream and cannot stop anything. This can. On a breach the
    # load is terminated by ITS OWN PID -- never by image name, which on this box has taken down
    # three production services.
    guard = LiveThermalGuard(events) if stream is not None else None
    if guard is not None:
        guard.start()
    else:
        row["notes"].append("live thermal guard NOT running: no b70tools stream for this cell")

    load_proc = subprocess.Popen(ff_argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, errors="replace", env=child_env)
    thermal_stop = None
    while True:
        try:
            load_stdout, load_stderr = load_proc.communicate(timeout=2)
            break
        except subprocess.TimeoutExpired:
            pass
        if guard is not None and guard.breach is not None:
            thermal_stop = guard.breach
            try:
                load_proc.terminate()
                load_stdout, load_stderr = load_proc.communicate(timeout=60)
            except Exception:  # noqa: BLE001
                load_proc.kill()
                load_stdout, load_stderr = load_proc.communicate()
            break
    ff_proc = subprocess.CompletedProcess(ff_argv, load_proc.returncode,
                                          stdout=load_stdout, stderr=load_stderr)
    cell_ended_wall = time.time()
    row["thermal_live"] = guard.stop() if guard is not None else {
        "polls": 0, "stopped_the_cell": False, "breach": None,
        "note": "no b70tools stream, so no live thermal guard ran"}
    if thermal_stop is not None:
        row["notes"].append("LIVE THERMAL ABORT at t+%ss: %s -- the load was terminated by pid"
                            % (thermal_stop.get("at_s"), thermal_stop.get("reason")))

    samples = poller.stop()
    status, body = _http("http://127.0.0.1:%d/metrics" % PRODUCTION_PORT, token)
    row["metrics_after"] = parse_prometheus(body) if status == 200 else {"http_status": status}
    row["n_busy_slots_per_decode_delta"] = busy_slots_delta(
        row["metrics_before"] if isinstance(row["metrics_before"], dict) else None,
        row["metrics_after"] if isinstance(row["metrics_after"], dict) else None)
    row["slots_poll"] = slot_busy(samples)
    row["slot_busy_fraction"] = row["slots_poll"].get("busy_slot_fraction")
    row["both_slots_busy_fraction"] = row["slots_poll"].get("all_busy_fraction")
    row["notes"].append(SPAN_VS_WINDOW_NOTE)
    row["commit_free_gb_after"] = telemetry.commit_free_gb()

    # --- ff_cell's row: the nine provenance fields, folded in, not re-derived --------
    ff_row = {}
    if ff_json.is_file():
        try:
            ff_row = json.loads(ff_json.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            row["notes"].append("ff_cell row unreadable: %s" % exc)
    row["ff_cell"] = {"argv": ff_argv, "returncode": ff_proc.returncode,
                      "stdout_tail": (ff_proc.stdout or "")[-2000:], "row": ff_row or None}
    for field in ("incumbent_process_epoch", "incumbent_restarted_since_cotenancy",
                  "incumbent_rate_fraction_pre", "incumbent_rate_fraction_post",
                  "placement_assertion", "placement_evidence", "health_gate_passed",
                  "receipt_status", "receipt_status_reason"):
        row[field] = ff_row.get(field)
    row["regime"]["placement"] = row["placement_assertion"]
    row["regime"]["epoch"] = row["incumbent_process_epoch"]

    # --- the load's own numbers, from summarize_rows -------------------------------
    rows_path = _harness_rows_path("sat-l1-" + cell)
    row["load_rows_path"] = str(rows_path) if rows_path else None
    harness_rows: list = []
    if rows_path and rows_path.is_file():
        harness_rows = _read_rows(rows_path)
        row["load_requests"] = len(harness_rows)
        summaries = _summarize(harness_rows)
        if summaries:
            summary = summaries[0]
            row["summary"] = summary
            for key in ("jobs_per_hour", "latency_p50_s", "latency_p95_s", "latency_p99_s",
                        "ttft_p50_s", "ttft_p95_s", "ttft_p99_s",
                        "decode_rate_p50_tokens_per_s", "prefill_rate_p50_tokens_per_s"):
                row[key] = summary.get(key)
    else:
        row["notes"].append("no harness rows at %s -- load fields stay null" % rows_path)

    # --- gate 4, the term that is NOT span-diluted ----------------------------------
    # metrics_before / the poller / metrics_after bracket the whole ff_cell subprocess,
    # which runs its own single-stream pre- and post-rate probes around the load. P5 asks
    # about the LOAD, so the gate is scored over [min(started_at), max(completed_at)] on
    # the load's own rows; the span figures stay beside it, labelled.
    window_t0, window_t1 = load_window_bounds(harness_rows)
    row["slots_poll_load_window"] = window_slot_busy(samples, window_t0, window_t1)
    row["slot_busy_fraction_load_window"] = row["slots_poll_load_window"].get("busy_slot_fraction")
    row["both_slots_busy_fraction_load_window"] = row["slots_poll_load_window"].get(
        "all_busy_fraction")

    # --- instrument (no gate): per-round launch skew from llama-server's own log -----
    # Read AFTER the load completes, so the tail already holds the cell's own launch lines.
    # The cell's ACTUAL prompt size is taken from the rows (440), never the requested figure
    # (512) -- filtering on 512 would match nothing at all. Any failure here is a loud null
    # in the field and a note; it never raises and never touches a gate.
    try:
        row["launch_skew"] = launch_skew_for_cell(harness_rows, expected_concurrency=n,
                                                  log_path=SERVE_LOG)
    except Exception as exc:  # noqa: BLE001 - instrumentation must never fail a cell
        row["launch_skew"] = {"rounds": None,
                              "reason": "launch skew instrument raised: %s" % exc,
                              "log_path": str(SERVE_LOG)}
    if (row["launch_skew"] or {}).get("rounds") is None:
        row["notes"].append("launch skew not recorded: %s"
                            % (row["launch_skew"] or {}).get("reason"))
    else:
        row["notes"].append(
            "launch skew: %d round(s), max %s ms, %d above %s ms -- a delayed round is a "
            "RECORDED OBSERVATION, not a failure; no gate reads this field"
            % (len(row["launch_skew"]["rounds"]), row["launch_skew"].get("max_skew_ms"),
               row["launch_skew"].get("delayed_rounds") or 0,
               row["launch_skew"].get("threshold_ms")))

    # --- gate 7: was the prefill real, or did the prompt cache serve it? -------------
    row["prefill_real"] = prefill_real(
        row["metrics_before"] if isinstance(row["metrics_before"], dict) else None,
        row["metrics_after"] if isinstance(row["metrics_after"], dict) else None,
        harness_rows)
    prefill_outcome = row["prefill_real"].get("outcome")
    if prefill_outcome in ("pass", "fail"):
        row["prefill_cached"] = prefill_outcome == "fail"

    # --- stream: wait for it to self-terminate, then reduce -------------------------
    if stream is not None:
        try:
            stream.wait(timeout=plan["stream_ticks"] + 120)
        except subprocess.TimeoutExpired:
            stream.kill()
            row["notes"].append("b70tools stream did not exit in time; killed")
        row["b70_stream"]["exit"] = stream.returncode
    window = cell_window_ns(harness_rows, launch_wall, launch_ns) if events.is_file() else None
    row["b70_stream"]["cell_window_ns"] = list(window) if window else None
    if events.is_file():
        try:
            row["power"] = satref.reduce_stream(events, "gpu", window)
        except Exception as exc:  # noqa: BLE001 - the loud failure, never a silent fallback
            row["power"] = {"error": str(exc)}
            row["notes"].append("power reduce failed: %s" % exc)
        try:
            row["budget_headroom"] = budget_headroom(events)
            row["over_admitted"] = row["budget_headroom"].get("over_admitted")
        except Exception as exc:  # noqa: BLE001
            row["notes"].append("budget headroom failed: %s" % exc)
        try:
            row["symmetry"] = satref.symmetry(events)
        except Exception as exc:  # noqa: BLE001
            row["notes"].append("symmetry failed: %s" % exc)
        try:
            row["reference"] = load_reference(Path(args.reference))
            row["duty_cycle"] = duty_cycle(card_intervals(events, "gpu"), row["reference"], window)
        except Exception as exc:  # noqa: BLE001
            row["notes"].append("duty cycle failed: %s" % exc)
        # --- gate 8: thermal, off the same stream ------------------------------------
        try:
            row["thermal"] = score_thermal(thermal_summary(events, window))
            row["thermal_exceeded"] = row["thermal"].get("exceeded")
            row["notes"].append("thermal: %s" % row["thermal"].get("detail"))
        except Exception as exc:  # noqa: BLE001 - a loud null, never a silent "cool"
            row["thermal"] = {"outcome": "null", "detail": "thermal reduction failed: %s" % exc}
            row["notes"].append("thermal reduction failed: %s" % exc)
    else:
        row["notes"].append("no b70tools events.jsonl -- power, duty cycle, thermal and "
                            "admission fields stay null")

    # --- gate 6: depth-0 ------------------------------------------------------------
    row["depth0"] = _depth0(cell, parts, python, cell_dir, rows_path)

    # --- gate 2, after: "after" means after the cell ENDED --------------------------
    try:
        row["guard_after"] = guard.wait_for_fresh(cell_ended_wall, "after cell",
                                                  timeout_s=args.settle_timeout,
                                                  state_reader=_rung_reader())
    except guard.ProductionDegraded as exc:
        row["guard_after"] = {"verdict": "stop", "error": str(exc)}
        return _finish(row, cell_dir, args, "STOPPED_AFTER_CELL", str(exc))

    # Gate 8 outranks the softer exclusions: a card that crossed the abort limit is the
    # loudest thing about this cell. The row is KEPT either way (like over_admitted) --
    # the status is what excludes it from the surface.
    # The LIVE guard outranks gate 8's post-hoc read, because it acted. A cell it stopped is
    # thermally exceeded whatever the finished stream later averages out to -- the load did not
    # run to completion, so its throughput is not a measurement of anything.
    live_breach = (row.get("thermal_live") or {}).get("breach")
    if live_breach:
        row["thermal_exceeded"] = True
        return _finish(row, cell_dir, args, "thermal_exceeded",
                       "LIVE thermal guard stopped the load at t+%ss: %s; row kept, excluded "
                       "from the surface, and the sweep halts"
                       % (live_breach.get("at_s"), live_breach.get("reason")))

    if row.get("thermal_exceeded"):
        return _finish(row, cell_dir, args, "thermal_exceeded",
                       "%s; row kept, excluded from the surface"
                       % ((row.get("thermal") or {}).get("detail") or "thermal abort"))

    partial = (row.get("symmetry") or {}).get("ratio")
    if partial is not None and partial < SYMMETRY_PARTIAL_BELOW:
        return _finish(row, cell_dir, args, "partially_scored",
                       "symmetry ratio %.3f below %.1f" % (partial, SYMMETRY_PARTIAL_BELOW))
    if row.get("over_admitted"):
        return _finish(row, cell_dir, args, "over_admitted",
                       "budget headroom went negative; kept, excluded from the surface")
    return _finish(row, cell_dir, args, "scored",
                   "cell completed with the incumbent healthy before and after")


def _finish(row: dict, cell_dir: Path, args, status: str, reason: str) -> dict:
    row["status"] = status
    if row.get("prefill_cached"):
        # Gate 7 marks the reason and never the status: the row is real data about a real
        # cell, KEPT and excluded from the surface, exactly like over_admitted.
        reason = ("%s; prefill cached -- gate 7 refused this cell's prefill (%s)"
                  % (reason, (row.get("prefill_real") or {}).get("reason")))
    row["status_reason"] = reason
    row["ts"] = dt.datetime.now(dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()
    row["gates"] = gate_outcomes(row)
    assert_receipt_complete(row)
    cell_dir.mkdir(parents=True, exist_ok=True)
    (cell_dir / "receipt.json").write_text(json.dumps(row, indent=1, ensure_ascii=False),
                                           encoding="utf-8")
    if not args.no_ledger:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with io.open(LEDGER, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def _git_head() -> str | None:
    try:
        return subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:  # noqa: BLE001
        return None


def _harness_rows_path(run_id: str) -> Path | None:
    try:
        sys.path.insert(0, str(REPO / "campaign" / "qwen38"))
        import qwen38_campaign  # noqa: PLC0415

        return qwen38_campaign.runtime_root() / "results" / "requests" / ("%s.jsonl" % run_id)
    except Exception:  # noqa: BLE001
        return None


def _read_rows(path: Path) -> list:
    rows = []
    with Path(path).open(encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def _summarize(rows: list) -> list:
    try:
        sys.path.insert(0, str(REPO / "campaign" / "qwen38"))
        import qwen38_campaign  # noqa: PLC0415

        return qwen38_campaign.summarize_rows(rows)
    except Exception:  # noqa: BLE001
        return []


ETW_RING_LIVE_S = 120.0   # a live circular session rewrites the ring continuously


def ring_is_live(ring: Path, now: float | None = None, max_age_s: float = ETW_RING_LIVE_S) -> tuple:
    """(live, age_s). A file that exists but has not been written for ``max_age_s`` is a DEAD ring.

    First live cell, 2026-09-09: the manifest from Aug 30 and the ring last written Sep 3 both
    existed, so a file-exists check passed, the runner copied 19 GB, and tracerpt ran for
    minutes before the packager (correctly) refused a Sep 9 arm against a Sep 4 span. Liveness is
    the session writing the ring, not the file being there.
    """
    try:
        age = (now if now is not None else time.time()) - os.path.getmtime(ring)
    except OSError:
        return False, None
    return age <= max_age_s, round(age, 1)


def _depth0(cell: str, parts: dict, python: str, cell_dir: Path, rows_path) -> dict:
    """Snapshot the ring, package it, read f0. Never starts or stops an ETW session."""
    base = {"f0": None, "f0_by_queue": None, "arms": None, "reason": None, "report": None}
    if not is_depth0_cell(parts["n"], parts["rep"]):
        base["reason"] = "not a depth-0 cell (N=2 and N=16, first repeat only)"
        return base
    if not ETW_SESSION_MANIFEST.is_file() or not ETW_RING.is_file():
        base["reason"] = ("no ETW session (%s). The runner never starts or stops tracing; "
                          "recorded as null, not dropped" % ETW_SESSION_MANIFEST)
        return base
    live, age_s = ring_is_live(ETW_RING)
    if not live:
        base["reason"] = ("ETW session not live: %s last written %s s ago (a live circular session "
                          "rewrites it continuously; the manifest is stale with it). A dead ring is "
                          "never snapshotted. Start the session (etw6_session.ps1 -Start, elevated) "
                          "-- the runner never does. Recorded as null, not dropped"
                          % (ETW_RING, age_s))
        base["ring_age_s"] = age_s
        return base
    if not rows_path or not Path(rows_path).is_file():
        base["reason"] = "no REAL harness rows for this cell; synthetic arms are never scored"
        return base
    try:
        sys.path.insert(0, str(REPO / "campaign" / "lz-probes"))
        import etw6_watch  # noqa: PLC0415

        manifest = etw6_watch.snapshot(cell, "sat-l1 depth-0 cell", {"probe": PROBE, "cell": cell})
    except Exception as exc:  # noqa: BLE001
        base["reason"] = "ring snapshot failed: %s" % exc
        return base
    cap_json = Path(manifest["etl"]).with_suffix(".json")
    etw_dir = cell_dir / "etw"
    etw_dir.mkdir(parents=True, exist_ok=True)
    report = etw_dir / "report.json"
    pkg = subprocess.run([python, str(ETW10), "--manifest", str(cap_json),
                          "--etl", str(manifest["etl"]), "--requests", str(rows_path),
                          "--max-dump-mb", "2048", "--out", str(report)],
                         capture_output=True, text=True, errors="replace")
    base["packager"] = {"returncode": pkg.returncode, "stdout_tail": (pkg.stdout or "")[-1500:],
                        "stderr_tail": (pkg.stderr or "")[-1500:]}
    if pkg.returncode != 0 or not report.is_file():
        base["reason"] = "etw10_package refused or produced no report; null, recorded"
        return base
    depth = subprocess.run([python, str(ETW4), str(report)], capture_output=True, text=True,
                           errors="replace")
    parsed = parse_etw4_depth(depth.stdout or "")
    base["arms"] = parsed.get("arms") or None
    unions = [v for v in (parsed.get("f0_union") or {}).values() if v is not None]
    base["f0_by_queue"] = parsed.get("arms") or None
    base["f0"] = unions[0] if len(unions) == 1 else (unions or None)
    base["report"] = str(report)
    base["manifest"] = str(cap_json)
    if base["f0"] is None:
        base["reason"] = "etw4_depth produced no parsable UNION depth0; null, recorded"
    return base


# ------------------------------------------------------------------- Phase 2: -np --
#: ``ArcServeRestart`` IS STOP-ONLY. It is not a restart despite the name: it ends the task
#: tree and nothing brings it back. ``fleet\arcserve\serve-arc.cmd``'s own header spells the
#: procedure out -- "ArcServeRestart (stop-only), delete the sentinel, then schtasks /Run /TN
#: ArcServeBoot". This module assumed the name and took production DOWN on 2026-09-09: the
#: yaml was edited, ArcServeRestart fired, ``wait_for_ready`` timed out, and nothing listened
#: on 8081 or 8082 with the serve log frozen. Running ArcServeBoot brought llama-swap back at
#: once and production ~3 min later.
ARCSERVE_STOP_TASK = "ArcServeRestart"
ARCSERVE_BOOT_TASK = "ArcServeBoot"
RECOVERY_COMMAND = "schtasks /Run /TN %s" % ARCSERVE_BOOT_TASK

#: The stop has to be OBSERVED, not assumed, before the boot is issued. llama-swap owns the
#: lifecycle on 8081 and the production upstream answers on 8082 (ADR-0045), so both must go
#: quiet; booting a second llama-swap while the first still holds 8081 would leave production
#: serving the PREVIOUS config while this runner recorded the new one -- exactly the silent
#: no-op class this lab exists to catch.
STOP_PORTS = (8081, PRODUCTION_PORT)
STOP_TIMEOUT_S = 60.0
STOP_POLL_S = 2.0


def port_listening(port: int, host: str = "127.0.0.1", timeout: float = 1.0,
                   connect=None) -> bool:
    """True when something accepts a TCP connection on ``host:port``.

    ``connect`` is the injection seam: a callable taking ``(host, port, timeout)`` that
    raises ``OSError`` when nothing is listening. Defaults to ``socket.create_connection``,
    so the real probe needs no network mocking to be tested -- pass a stub instead.
    """
    opener = socket.create_connection if connect is None else connect
    try:
        sock = opener((host, int(port)), timeout)
    except OSError:
        return False
    try:
        close = getattr(sock, "close", None)
        if callable(close):
            close()
    except OSError:  # pragma: no cover - closing a probe socket is not a measurement
        pass
    return True


def wait_for_stop(ports=STOP_PORTS, timeout_s: float = STOP_TIMEOUT_S,
                  poll_s: float = STOP_POLL_S, probe=port_listening,
                  sleep=time.sleep, clock=time.monotonic) -> dict:
    """Poll until nothing listens on any of ``ports``, or ``timeout_s`` expires.

    Bounded and recorded either way: a timeout is reported with the ports still listening,
    never swallowed. ``probe`` / ``sleep`` / ``clock`` are injectable so this is exercised
    without touching the network or the wall clock.
    """
    ports = tuple(ports)
    started = clock()
    polls = 0
    listening = list(ports)
    while True:
        polls += 1
        listening = [p for p in ports if probe(p)]
        waited = clock() - started
        if not listening:
            return {"stopped": True, "ports": list(ports), "still_listening": [],
                    "polls": polls, "waited_s": round(waited, 2), "timeout_s": timeout_s,
                    "detail": "nothing listens on %s -- the stop took effect"
                              % ", ".join(str(p) for p in ports)}
        if waited >= timeout_s:
            return {"stopped": False, "ports": list(ports), "still_listening": listening,
                    "polls": polls, "waited_s": round(waited, 2), "timeout_s": timeout_s,
                    "detail": "still listening on %s after %.0fs -- the stop did NOT take "
                              "effect within the bound"
                              % (", ".join(str(p) for p in listening), timeout_s)}
        sleep(poll_s)


def set_np(np_slots: int, python: str, args) -> dict:
    """The whole Phase 2 sequence for one ``-np`` value. A tenancy call -- Derek's.

    Refused outright while the shared maintenance sentinel exists, and after any
    ``preflight`` NO-GO. Each step is recorded so the restart itself is auditable.

    THE SEQUENCE IS stop -> observe the stop -> boot, not "restart". ``ArcServeRestart`` only
    tears the tree down (see ``ARCSERVE_STOP_TASK``), so ``ArcServeBoot`` is issued explicitly
    afterwards and ``wait_for_ready`` gates on the marker the NEW epoch writes.

    THE SENTINEL IS NEVER DELETED HERE. ``hearth\\var\\arc-maintenance.stop`` is a SHARED
    lock (imagegen holds it too). This function refuses to start while it exists, and if it
    appears between the preflight and the stop it aborts BEFORE booting rather than booting
    into a lock someone else is holding. Dropping another tenant's lock on their behalf is
    not this code's call -- the operator clears it and re-runs.
    """
    record = {"np": np_slots, "ub": UB, "steps": [], "ok": False,
              "production_may_be_down": False, "recovery_command": None}
    if MAINTENANCE_STOP.exists():
        record["steps"].append({"step": "sentinel", "ok": False,
                                "detail": "%s exists -- a shared maintenance lock is held; "
                                          "refusing to restart production" % MAINTENANCE_STOP})
        return record
    pre = subprocess.run([python, "-m", "hearth.rotation.preflight",
                          "--models", MODEL_ALIAS, "--json"],
                         capture_output=True, text=True, errors="replace", cwd=str(REPO))
    go = pre.returncode == 0
    record["steps"].append({"step": "preflight", "ok": go, "returncode": pre.returncode,
                            "stdout_tail": (pre.stdout or "")[-1200:]})
    if not go:
        record["steps"].append({"step": "abort", "ok": False, "detail": "preflight NO-GO"})
        return record

    text = read_exact(OMEN_YAML)
    new_text, old = edit_np_yaml(text, np_slots)
    (Path(args.out) / "np-backups").mkdir(parents=True, exist_ok=True)
    backup = Path(args.out) / "np-backups" / ("omen.yaml.np%d.%s" % (
        old, dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")))
    write_exact(backup, text)
    write_exact(OMEN_YAML, new_text)
    record["steps"].append({"step": "yaml", "ok": True, "from_np": old, "to_np": np_slots,
                            "backup": str(backup), "path": str(OMEN_YAML),
                            "detail": "only the -np token inside the %r block changed"
                                      % PRODUCTION_MODEL_KEY})

    # --- 1/3: the STOP. ArcServeRestart ends the tree and does not bring it back. -------
    stop = subprocess.run(["powershell", "-NoProfile", "-Command",
                           "schtasks /Run /TN %s" % ARCSERVE_STOP_TASK],
                          capture_output=True, text=True, errors="replace")
    record["steps"].append({"step": "stop", "ok": stop.returncode == 0,
                            "task": ARCSERVE_STOP_TASK,
                            "returncode": stop.returncode,
                            "stdout_tail": (stop.stdout or "")[-400:],
                            "detail": "%s is STOP-ONLY -- it tears the task tree down and "
                                      "NOTHING restarts it; %s is issued below "
                                      "(fleet/arcserve/serve-arc.cmd's own header). schtasks "
                                      "via PowerShell, never Git Bash"
                                      % (ARCSERVE_STOP_TASK, ARCSERVE_BOOT_TASK)})

    # --- 2/3: observe the stop, bounded. A boot on top of a live llama-swap would leave
    #          production on the OLD config while this record claimed the new one. --------
    stopped = wait_for_stop()
    record["steps"].append({"step": "wait_for_stop", "ok": bool(stopped.get("stopped")),
                            "wait": stopped, "detail": stopped.get("detail")})

    # --- the sentinel, re-read AFTER the stop: a lock taken while we were stopping means
    #     production stays down until its holder is finished. Booting into it is not ours
    #     to do, and neither is deleting it. --------------------------------------------
    if MAINTENANCE_STOP.exists():
        record["production_may_be_down"] = True
        record["recovery_command"] = RECOVERY_COMMAND
        record["steps"].append({
            "step": "abort_before_boot", "ok": False,
            "detail": "%s appeared between the preflight and the stop. Production is now "
                      "STOPPED and this run refuses to boot into a held maintenance lock. "
                      "The sentinel is a SHARED lock and is NOT deleted here: when its "
                      "holder releases it, bring production back with `%s`."
                      % (MAINTENANCE_STOP, RECOVERY_COMMAND)})
        return record

    # --- 3/3: the BOOT. ``since`` is taken here, not before the stop: the stop itself
    #          writes shutdown lines, so a ``since`` from before it would let the PREVIOUS
    #          epoch's "model loaded" satisfy the marker check. -------------------------
    issued = dt.datetime.now()
    boot = subprocess.run(["powershell", "-NoProfile", "-Command",
                           "schtasks /Run /TN %s" % ARCSERVE_BOOT_TASK],
                          capture_output=True, text=True, errors="replace")
    record["steps"].append({"step": "boot", "ok": boot.returncode == 0,
                            "task": ARCSERVE_BOOT_TASK,
                            "returncode": boot.returncode,
                            "stdout_tail": (boot.stdout or "")[-400:],
                            "detail": "the half %s does not do; llama-swap comes back on "
                                      "8081 and preloads the production upstream on %d"
                                      % (ARCSERVE_STOP_TASK, PRODUCTION_PORT)})

    ready = ff_cell.wait_for_ready(since=issued)
    record["steps"].append({"step": "ready_marker", "ok": bool(ready),
                            "detail": "ff_cell.wait_for_ready on the REAL %r marker in "
                                      "arc-serve.log" % ff_cell.READY_MARKER})
    if not ready:
        record["production_may_be_down"] = True
        record["recovery_command"] = RECOVERY_COMMAND
        record["steps"].append({
            "step": "production_may_be_down", "ok": False,
            "detail": "PRODUCTION MAY BE DOWN. %s was stopped and %s was issued, but no %r "
                      "marker appeared in %s within the timeout. Check whether anything "
                      "listens on %s; if not, recover with `%s` and re-check /health and a "
                      "real completion before treating the rung as available."
                      % (ARCSERVE_STOP_TASK, ARCSERVE_BOOT_TASK, ff_cell.READY_MARKER,
                         SERVE_LOG, " or ".join(str(p) for p in STOP_PORTS),
                         RECOVERY_COMMAND)})
        return record
    health_ok, completion = _health_and_completion()
    record["steps"].append({"step": "health_and_completion", "ok": bool(health_ok and completion),
                            "health_200": bool(health_ok), "completion": completion,
                            "detail": "/health 200 AND a real completion carrying timings -- "
                                      "port-open is not model-ready"})
    if not (health_ok and completion):
        return record

    note = "sat-l1 -np %d -ub %d (production entry, -c %d => %d tok/slot)" % (
        np_slots, UB, TOTAL_CTX, per_slot_ctx(np_slots))
    base = subprocess.run([python, str(HERE / "ff_ratecheck.py"), "--rung", "omen-arc",
                           "--set-baseline", "--note", note],
                          capture_output=True, text=True, errors="replace")
    record["steps"].append({"step": "set_baseline", "ok": base.returncode == 0,
                            "note": note, "stdout_tail": (base.stdout or "")[-1200:]})
    if base.returncode != 0:
        return record
    doc = json.loads(BASELINES.read_text(encoding="utf-8-sig"))
    new_baseline = ((doc.get("rungs") or {}).get("omen-arc") or {}).get("baseline_decode_tok_s")
    boundary = epoch_boundary_row(
        dt.datetime.now(dt.timezone.utc).astimezone().replace(microsecond=0).isoformat(),
        np_slots, new_baseline)
    append_epoch_boundary(BASELINES, boundary)
    record["steps"].append({"step": "epoch_boundary", "ok": True, "row": boundary})

    rung = (json.loads(BASELINES.read_text(encoding="utf-8-sig")).get("rungs") or {}).get("omen-arc")
    warm = warm_to_flatness(lambda: ff_ratecheck.measure(rung, FLATNESS_REPS))
    record["steps"].append({"step": "warm", "ok": bool(warm.get("flat")), "warm": warm})
    record["ok"] = bool(warm.get("flat"))
    return record


def _health_and_completion() -> tuple:
    status, _ = _http("http://127.0.0.1:%d/health" % PRODUCTION_PORT, None, timeout=10.0)
    if status != 200:
        return False, {"error": "health HTTP %s" % status}
    token = _token()
    body = json.dumps({"prompt": "ready?", "n_predict": 8, "temperature": 0,
                       "cache_prompt": False}).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:%d/completion" % PRODUCTION_PORT,
                                 data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer %s" % token)
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            doc = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return True, {"error": str(exc)[:200]}
    timings = doc.get("timings") or {}
    if not timings:
        return True, {"error": "no timings block -- served during load?"}
    return True, {"predicted_per_second": timings.get("predicted_per_second"),
                  "prompt_per_second": timings.get("prompt_per_second")}


# ------------------------------------------------------------------------------ CLI --
def _print_plan(plan: dict) -> None:
    print("=== SAT-L1 DRY RUN -- nothing executed: no network, no GPU, no file written ===")
    if REPO != LIVE_ROOT:
        print("*** running from %s, NOT the live checkout %s." % (REPO, LIVE_ROOT))
        print("    Machine state (omen.yaml, the maintenance sentinel, the keep-alive tail) is "
              "read from the LIVE root; source is read from here. Run live cells -- and every "
              "Phase 2 restart -- from the live checkout, or a yaml edit is a silent no-op.")
    print("cell           %s   (phase %d)" % (plan["cell"], plan["phase"]))
    print("regime         %s" % json.dumps(plan["regime"], sort_keys=True))
    print("repeats        %d planned for this (np, depth, N); ratecheck reps %d"
          % (plan["repeats_planned"], plan["reps_ratecheck"]))
    print("bearer         %s present=%s length=%d -> %s"
          % (plan["bearer"]["env"], plan["bearer"]["present"], plan["bearer"]["length"],
             HARNESS_KEY_ENV))
    print("               value never printed, never logged, never written to a receipt")
    print("cell dir       %s" % plan["cell_dir"])
    print("stream         %d ticks at 1 Hz (estimated cell %d s)"
          % (plan["stream_ticks"], plan["estimated_cell_seconds"]))
    print("depth-0 cell   %s" % plan["depth0_cell"])
    print("")
    for step in plan["steps"]:
        print("  [%2d] %s" % (step["step"], step["what"]))
        if step.get("argv"):
            print("       $ %s" % " ".join(shlex.quote(str(a)) for a in step["argv"]))
        if step.get("env"):
            print("       env %s" % step["env"])
        if step.get("how"):
            print("       %s" % step["how"])
        if step.get("note"):
            print("       note: %s" % step["note"])
    print("")
    print("receipt        %s" % plan["receipt"])
    print("ledger         %s (probe=%s, one row per cell)" % (plan["ledger"], PROBE))
    print("gates          %s" % ", ".join("%d:%s" % g for g in GATES))


def _dry_gate_preview(args) -> None:
    """The gate decisions, from PASSIVE readers only -- no /health probe, no GPU."""
    print("")
    print("--- gate decisions as they read RIGHT NOW (passive readers only) ---")
    try:
        state = _rung_reader()()
        print("  gate 2 production_guard : rungstate verdict=%s observed=%s frac=%s age=%ss"
              % (state.get("verdict"), state.get("observed_tok_s"),
                 state.get("frac_of_baseline"), state.get("observed_age_s")))
        print("                            live gate ALSO probes /health; a stop verdict "
              "(degraded/stalled/unreachable) raises, and anything but at_rate refuses the cell")
    except Exception as exc:  # noqa: BLE001
        print("  gate 2 production_guard : rungstate unreadable (%s)" % exc)
    print("  gate 1 warm_then_measure: would loop ff_ratecheck.measure until spread <= %.1f%% "
          "(max %d)" % (FLATNESS_SPREAD_PCT, FLATNESS_MAX_ITERATIONS))
    print("  gate 3 admission        : b70tools budget-usage headroom; negative => over_admitted "
          "(row kept, excluded)")
    print("  gate 4 in_flight        : /slots at 1 Hz + %s delta; scored over the LOAD "
          "window (min started_at .. max completed_at on the load's own rows), with the "
          "span figures -- which include ff_cell's single-stream pre/post probes -- kept "
          "beside it" % BUSY_SLOTS_SERIES)
    try:
        ref = load_reference(Path(args.reference))
        for key, card in sorted(ref["cards"].items()):
            print("  gate 5 duty_cycle       : %s (%s) reference burst p50 %s W -> threshold %s W"
                  % (key, card.get("adapter"), card.get("burst_p50_w"),
                     round(DUTY_THRESHOLD_FRAC * card["burst_p50_w"], 2)
                     if card.get("burst_p50_w") else None))
        print("                            read from %s, keyed by %s -- never hardcoded"
              % (ref["source"], ref["keyed_by"]))
        window = ref.get("burst_window") or {}
        print("                            DENOMINATOR: a %s s burst at %s clients (%s "
              "one-second intervals measured)"
              % (window.get("declared_s"), window.get("clients"),
                 window.get("measured_intervals_s")))
        print("                            CAVEAT: %s" % DUTY_REFERENCE_CAVEAT)
    except Exception as exc:  # noqa: BLE001
        print("  gate 5 duty_cycle       : frozen reference unreadable (%s) -> duty null" % exc)
    print("  gate 6 depth0_fraction  : session manifest %s -> %s"
          % (ETW_SESSION_MANIFEST, "present" if ETW_SESSION_MANIFEST.is_file()
             else "ABSENT; depth-0 cells carry null and tracing is never started"))
    print("  gate 7 prefill_real     : the load carries --no-cache-prompt; delta %s must be "
          ">= %.2f x the prompt tokens the cell's own rows report (delta %s is reported "
          "beside it). A fail marks status_reason and KEEPS the row"
          % (PROMPT_TOKENS_SERIES, PREFILL_REAL_MIN_FRACTION, PROMPT_TOKENS_CACHED_SERIES))
    print("  gate 8 thermal          : %s from the same b70tools stream; abort %.0f C / warn "
          "%.0f C absolute (GPU and VRAM alike), delta warn %.0f C. %s"
          % (", ".join(THERMAL_COUNTERS), VRAM_ABORT_C, VRAM_WARN_C, DELTA_WARN_C,
             THERMAL_ATTRIBUTION))
    print("                            %s" % THERMAL_AMBIENT_NOTE)
    print("                            LIVE GUARD (2026-09-09): a thermal watchdog polls this "
          "stream every %.0fs WHILE the load runs and terminates the load by pid on a breach. "
          "Gate 8 below scores the FINISHED stream and cannot stop anything. A thermally "
          "exceeded cell now HALTS THE SWEEP -- it did not before, so the next cell used to "
          "launch onto a card that had just hit %.0f C. Cooldown line %.0f C is printed, not "
          "enforced." % (5.0, GPU_ABORT_C, THERMAL_RESUME_BELOW_C))
    print("                            a fail marks status_reason and thermal_exceeded and "
          "KEEPS the row, exactly like over_admitted")
    print("  maintenance sentinel    : %s -> %s"
          % (MAINTENANCE_STOP, "PRESENT (Phase 2 restarts refused)"
             if MAINTENANCE_STOP.exists() else "absent"))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--one-cell", default=None, metavar="CELL",
                    help="run exactly one cell, e.g. np2-p512-c2-r1")
    ap.add_argument("--sweep", action="store_true",
                    help="the full planned sweep (explicit; never the default)")
    ap.add_argument("--phase", type=int, default=1, choices=(1, 2),
                    help="1 = the -np 2 surface; 2 = the -np {4,8} restarts (a tenancy call)")
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="print the command chain and gate decisions; THE DEFAULT")
    ap.add_argument("--live", dest="dry_run", action="store_false",
                    help="actually run. The operator runs live cells, not the builder.")
    ap.add_argument("--reps", type=int, default=3, help="ff_ratecheck reps per pre/post rate")
    ap.add_argument("--out", default=str(CELLS_ROOT))
    ap.add_argument("--reference", default=str(REFERENCE_RECEIPT),
                    help="the FROZEN SAT-L1 saturation reference receipt")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--cell-seconds", type=int, default=0, help="override the stream sizing")
    ap.add_argument("--stream-ticks", type=int, default=0)
    ap.add_argument("--settle-timeout", type=float, default=420.0)
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument("--set-np", type=int, default=None,
                    help="Phase 2 only: restart production at this -np (tenancy call)")
    ap.add_argument("--restore-np", action="store_true",
                    help="restore -np %d at the end of a run" % BASE_NP)
    return ap


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)
    python = args.python

    if args.set_np is not None or args.restore_np:
        target = BASE_NP if args.restore_np else args.set_np
        if args.dry_run:
            print("=== SAT-L1 DRY RUN -- Phase 2 -np sequence, nothing executed ===")
            if REPO != LIVE_ROOT:
                print("*** running from %s, NOT the live checkout %s. omen.yaml and the "
                      "sentinel are read from the LIVE root, but ff_ratecheck and its "
                      "rate-baselines.json resolve beside THIS copy. Run Phase 2 from the "
                      "live checkout so the baseline and the epoch boundary land in the file "
                      "the rest of the lab reads." % (REPO, LIVE_ROOT))
            print("  sentinel  %s -> %s" % (MAINTENANCE_STOP,
                                            "PRESENT: refused" if MAINTENANCE_STOP.exists()
                                            else "absent"))
            print("  $ %s -m hearth.rotation.preflight --models %s --json  (NO-GO stops)"
                  % (python, MODEL_ALIAS))
            print("  edit %s: the -np token inside the %r block only, %s -> %d"
                  % (OMEN_YAML, PRODUCTION_MODEL_KEY,
                     read_np_yaml(read_exact(OMEN_YAML)), target))
            print("  $ powershell -NoProfile -Command \"schtasks /Run /TN %s\"   "
                  "# STOP-ONLY: this does NOT bring production back"
                  % ARCSERVE_STOP_TASK)
            print("  wait (max %.0fs): poll until nothing listens on %s"
                  % (STOP_TIMEOUT_S, ", ".join(str(p) for p in STOP_PORTS)))
            print("  re-read the sentinel: if it appeared while stopping, ABORT here -- "
                  "production stays down until its holder clears it, and this runner never "
                  "deletes a shared lock")
            print("  $ powershell -NoProfile -Command \"schtasks /Run /TN %s\"   "
                  "# the half the stop does not do" % ARCSERVE_BOOT_TASK)
            print("  wait: the REAL %r marker, then /health 200 AND a real completion"
                  % ff_cell.READY_MARKER)
            print("  on a marker timeout the record says PRODUCTION MAY BE DOWN and names "
                  "`%s`" % RECOVERY_COMMAND)
            print("  $ %s %s --rung omen-arc --set-baseline --note "
                  "\"sat-l1 -np %d -ub %d (production entry, -c %d => %d tok/slot)\""
                  % (python, HERE / "ff_ratecheck.py", target, UB, TOTAL_CTX,
                     per_slot_ctx(target)))
            print("  append one row to epoch_boundaries in %s:" % BASELINES)
            print("    %s" % json.dumps(epoch_boundary_row("<ts>", target, "<new baseline>")))
            print("  then warm to flatness before the first cell of the block")
            return 0
        record = set_np(target, python, args)
        print(json.dumps(record, indent=1, ensure_ascii=False))
        return 0 if record.get("ok") else 1

    if args.one_cell:
        cells = [args.one_cell]
    elif args.sweep:
        cells = plan_cells(args.phase)
    else:
        print("nothing selected. Use --one-cell <cell> or --sweep (the full sweep is never "
              "implicit). --dry-run is the default; --live is the explicit opt-in.")
        return 2

    if args.dry_run:
        for i, cell in enumerate(cells):
            if i:
                print("")
            _print_plan(plan_cell(cell, python, args))
        if len(cells) == 1:
            _dry_gate_preview(args)
        else:
            print("")
            print("%d cells planned, depth blocks outermost and N ascending." % len(cells))
        return 0

    rc = 0
    for cell in cells:
        row = run_cell(cell, python, args)
        print(json.dumps({k: row[k] for k in ("cell", "status", "status_reason", "jobs_per_hour",
                                              "slot_busy_fraction", "over_admitted")},
                         ensure_ascii=False))
        # ``thermal_exceeded`` added 2026-09-09. It was NOT here, which meant a cell that reached
        # the 95 C abort line was marked, excluded from the surface, and then the NEXT CELL
        # LAUNCHED -- onto a card that had just hit the limit. Gate 8 protected the dataset and
        # nothing protected the cards. A cooldown is a human decision, so this stops and says so.
        if row["status"] in ("REFUSED_GUARD", "STOPPED_AFTER_CELL", "REFUSED_NOT_WARM",
                             "thermal_exceeded"):
            print("STOP: %s -- %s" % (row["status"], row["status_reason"]))
            if row["status"] == "thermal_exceeded":
                print("      Cards reached the abort line. Let them cool below %.0f C before the "
                      "next cell -- the 2026-08-27 harness held that resume line and this runner "
                      "does not enforce one." % THERMAL_RESUME_BELOW_C)
            rc = 1
            break
    if args.restore_np:
        print(json.dumps(set_np(BASE_NP, python, args), indent=1, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

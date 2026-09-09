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
  3. Admission: budget headroom per card from the b70tools stream; negative -> the row is
     flagged ``over_admitted`` and KEPT (excluded from the surface, never dropped).
  4. Phase 2 ``-np``: token-exact edit of the PRODUCTION entry in
     ``fleet/arcserve/llama-swap/omen.yaml`` -> ``schtasks /Run /TN ArcServeRestart`` via
     PowerShell -> ``/health`` 200 AND a real completion -> ``ff_ratecheck --set-baseline``
     whose ``--note`` names ``-np`` and ``-ub`` -> a row appended to ``epoch_boundaries`` ->
     warm. ``-np 2`` restored at the end. Refused outright while
     ``hearth\var\arc-maintenance.stop`` exists.
  5. Board duty cycle against the FROZEN reference receipt (read from the receipt, never
     hardcoded): the fraction of CELL WALL TIME each card's dJ/dt is above 0.9x ITS OWN
     reference burst p50, per card, keyed by PCI BDF.

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
import os
import re
import shlex
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
BASE_NP = 2                 # production's standing value, restored at the end of a sweep
UB = 1024                   # production's -ub, named in every re-baseline note

#: ADR-0043 warm gate. Three consecutive reps within +-2% (the card's gate 1).
FLATNESS_SPREAD_PCT = 2.0
FLATNESS_REPS = 3
FLATNESS_MAX_ITERATIONS = 6

#: Duty cycle: above 0.9x THIS card's own frozen reference burst p50 (card, gate 5).
DUTY_THRESHOLD_FRAC = 0.9
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
    "load_command", "load_rows_path", "load_requests", "summary",
    "jobs_per_hour", "latency_p50_s", "latency_p95_s", "latency_p99_s",
    "ttft_p50_s", "ttft_p95_s", "ttft_p99_s",
    "decode_rate_p50_tokens_per_s", "prefill_rate_p50_tokens_per_s",
    # --- gate 4: in-flight ----------------------------------------------------------------
    "slots_poll", "slot_busy_fraction", "both_slots_busy_fraction",
    "metrics_before", "metrics_after", "n_busy_slots_per_decode_delta",
    # --- gate 3: admission ------------------------------------------------------------------
    "commit_free_gb_before", "commit_free_gb_after", "budget_headroom", "over_admitted",
    # --- gate 5: board duty cycle against the frozen reference ---------------------------
    "reference", "power", "duty_cycle", "symmetry", "b70_stream",
    # --- gate 6: depth-0 ---------------------------------------------------------------------
    "depth0",
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
    if np_slots == BASE_NP and depth == 512 and n == 2:
        return 5
    if np_slots == BASE_NP and n >= 4:
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
    nps = (BASE_NP,) if phase == 1 else PHASE2_NP
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
def slot_busy(samples: list) -> dict:
    """Slot occupancy from 1 Hz ``/slots`` polls.

    A sample is ``{"t": <wall epoch>, "slots": [...]}`` or ``{"t": ..., "error": "..."}``.
    Fractions are over the OK polls only; with none, every fraction is ``None`` rather
    than 0.0 -- ``null != 0`` is a campaign invariant.
    """
    ok = [s for s in samples or () if isinstance(s.get("slots"), list)]
    errors = [s for s in samples or () if not isinstance(s.get("slots"), list)]
    if not ok:
        return {"polls": len(samples or ()), "ok_polls": 0, "error_polls": len(errors),
                "slots_seen": None, "any_busy_fraction": None, "all_busy_fraction": None,
                "mean_busy_slots": None, "busy_slot_fraction": None,
                "error_sample": (errors[0].get("error") if errors else None)}
    busy_counts, totals, per_poll_frac = [], [], []
    for s in ok:
        slots = s["slots"]
        busy = sum(1 for slot in slots if slot.get("is_processing"))
        busy_counts.append(busy)
        totals.append(len(slots))
        per_poll_frac.append((busy / len(slots)) if slots else 0.0)
    slots_seen = max(totals) if totals else None
    n = len(ok)
    return {
        "polls": len(samples or ()),
        "ok_polls": n,
        "error_polls": len(errors),
        "slots_seen": slots_seen,
        "any_busy_fraction": round(sum(1 for b in busy_counts if b >= 1) / n, 4),
        "all_busy_fraction": round(
            sum(1 for b, t in zip(busy_counts, totals) if t and b == t) / n, 4),
        "mean_busy_slots": round(sum(busy_counts) / n, 4),
        "busy_slot_fraction": round(sum(per_poll_frac) / n, 4),
        "error_sample": (errors[0].get("error") if errors else None),
    }


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
    return {"source": str(path), "counter": power.get("counter"),
            "keyed_by": "bdf" if bdf_by_adapter else "adapter",
            "frozen": "2026-09-09 compute control; the render half is not applicable "
                      "(lane paused under the Hatchet cutover; compute 0.0% during encode)",
            "cards": cards}


def duty_cycle(cards: dict, reference: dict, window_ns: tuple | None = None,
               threshold_frac: float = DUTY_THRESHOLD_FRAC) -> dict:
    """Fraction of WALL TIME each card's dJ/dt is above ``threshold_frac`` x its OWN ref p50.

    Time-weighted, per card, keyed by BDF. Intervals are counted only when they lie wholly
    inside ``window_ns``; one straddling an edge is counted in neither and reported. A card
    with no reference entry, or a reference with no burst p50, yields ``None`` -- never a
    number derived from the other card's reference.
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
                          "%.2fx its own frozen reference burst p50" % threshold_frac}


# ------------------------------------------------------------------------ admission --
BUDGET = "vram.local.budget_bytes"
USAGE = "vram.local.current_usage_bytes"
NON_LOCAL_USAGE = "vram.non_local.current_usage_bytes"
_GB = 1024.0 ** 3


def budget_headroom(events_path: Path) -> dict:
    """Live ``QueryVideoMemoryInfo`` headroom per card: budget - current usage.

    Both series are emitted sparsely by this b70tools build, so each is forward-filled and
    headroom is evaluated at every tick where BOTH are known. ``over_admitted`` is True when
    any card's headroom goes negative, ``None`` when the stream never carried the pair --
    unknown is recorded as unknown, and the row is kept either way (the card, gate 3).
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
            elif row.get("k") == "ms" and row.get("n") in (BUDGET, USAGE, NON_LOCAL_USAGE):
                series.setdefault(row.get("a"), []).append(
                    (int(row.get("t") or 0), row["n"], float(row["v"])))
    cards: dict = {}
    for adapter, rows in series.items():
        info = ident.get(adapter) or {}
        if "B70" not in (info.get("desc") or ""):
            continue
        rows.sort()
        budget = usage = non_local = None
        headrooms: list[float] = []
        for _t, name, value in rows:
            if name == BUDGET:
                budget = value
            elif name == USAGE:
                usage = value
            else:
                non_local = value
            if budget is not None and usage is not None:
                headrooms.append(budget - usage)
        key = info.get("bdf") or adapter
        cards[key] = {
            "adapter": adapter, "bdf": info.get("bdf"), "desc": info.get("desc"),
            "budget_gb": round(budget / _GB, 3) if budget is not None else None,
            "usage_gb": round(usage / _GB, 3) if usage is not None else None,
            "non_local_usage_gb": round(non_local / _GB, 3) if non_local is not None else None,
            "samples": len(headrooms),
            "min_headroom_gb": round(min(headrooms) / _GB, 3) if headrooms else None,
            "last_headroom_gb": round(headrooms[-1] / _GB, 3) if headrooms else None,
        }
    negatives = [c["min_headroom_gb"] for c in cards.values() if c["min_headroom_gb"] is not None]
    over = None if not negatives else any(v < 0 for v in negatives)
    return {"cards": cards, "over_admitted": over,
            "note": "budget - current usage from the live QueryVideoMemoryInfo series; "
                    "sparse in this b70tools build, so both series are forward-filled. "
                    "An over_admitted row is KEPT and excluded from the surface, not dropped."}


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
    """All six gates, always all six, each with an outcome and its reason."""
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
            if not poll or not poll.get("ok_polls"):
                outcome, detail = "null", "no successful /slots poll"
            else:
                outcome = "pass"
                detail = ("%d polls, all-slots-busy %s, busy-slots/decode delta %s"
                          % (poll.get("ok_polls"), poll.get("all_busy_fraction"),
                             row.get("n_busy_slots_per_decode_delta")))
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
        out.append({"gate": number, "name": name, "outcome": outcome, "detail": detail})
    return out


# --------------------------------------------------------------- command construction --
def load_argv(python: str, cell: str, depth: int, n: int, run_id: str | None = None) -> list:
    """The card's load-generator invocation, verbatim, with this cell's knobs."""
    return [python, str(QWEN38), "load",
            "--run-id", run_id or ("sat-l1-" + cell),
            "--endpoint", "http://127.0.0.1:%d" % PRODUCTION_PORT,
            "--candidate", CANDIDATE, "--topology", TOPOLOGY, "--model", MODEL_ALIAS,
            "--concurrency", str(n), "--prompt-tokens", str(depth),
            "--max-tokens", str(MAX_TOKENS),
            "--requests-per-client", str(REQUESTS_PER_CLIENT),
            "--seed", str(SEED), "--disable-thinking"]


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
        "cell": cell, "phase": 1 if np_slots == BASE_NP else 2,
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
            {"step": 9, "what": "wait for the b70tools stream to exit, then reduce",
             "how": "sat_reference_capture.reduce_stream(events, 'gpu', window_ns) + "
                    "card_intervals(events) for the time-weighted duty cycle"},
            {"step": 10, "what": "symmetry gate",
             "argv": [python, str(VERDICT_PY), str(cell_dir / "b70" / "events.jsonl"), "--json"],
             "note": "ratio < %.1f -> the row is partially_scored" % SYMMETRY_PARTIAL_BELOW},
            {"step": 11, "what": "duty cycle against the FROZEN reference",
             "how": "per card, fraction of cell wall time dJ/dt > %.2f x its own reference "
                    "burst p50, read from %s" % (DUTY_THRESHOLD_FRAC, REFERENCE_RECEIPT)},
            {"step": 12, "what": "depth-0 (ETW4)",
             "how": _depth0_plan(cell, parts, python, cell_dir)},
            {"step": 13, "what": "guard.wait_for_fresh after the cell ENDED",
             "how": "a rung sample whose reading predates the cell is `unknown`, never a "
                    "pass; a sample taken during the cell is kept as rung_during"},
            {"step": 14, "what": "write ONE receipt and append ONE ledger row",
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
                "load_rows_path": None, "status": "running"})

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
        return _finish(row, cell_dir, args, "REFUSED_NOT_WARM",
                       "the rung never settled within %.1f%%; ADR-0043 says warm, do not restart"
                       % FLATNESS_SPREAD_PCT)
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
    ff_proc = subprocess.run(ff_argv, capture_output=True, text=True, errors="replace",
                             env=child_env)
    cell_ended_wall = time.time()

    samples = poller.stop()
    status, body = _http("http://127.0.0.1:%d/metrics" % PRODUCTION_PORT, token)
    row["metrics_after"] = parse_prometheus(body) if status == 200 else {"http_status": status}
    row["n_busy_slots_per_decode_delta"] = busy_slots_delta(
        row["metrics_before"] if isinstance(row["metrics_before"], dict) else None,
        row["metrics_after"] if isinstance(row["metrics_after"], dict) else None)
    row["slots_poll"] = slot_busy(samples)
    row["slot_busy_fraction"] = row["slots_poll"].get("busy_slot_fraction")
    row["both_slots_busy_fraction"] = row["slots_poll"].get("all_busy_fraction")
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
    else:
        row["notes"].append("no b70tools events.jsonl -- power, duty cycle and admission "
                            "fields stay null")

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
def set_np(np_slots: int, python: str, args) -> dict:
    """The whole Phase 2 sequence for one ``-np`` value. A tenancy call -- Derek's.

    Refused outright while the shared maintenance sentinel exists, and after any
    ``preflight`` NO-GO. Each step is recorded so the restart itself is auditable.
    """
    record = {"np": np_slots, "ub": UB, "steps": [], "ok": False}
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

    issued = dt.datetime.now()
    restart = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "schtasks /Run /TN ArcServeRestart"],
                             capture_output=True, text=True, errors="replace")
    record["steps"].append({"step": "restart", "ok": restart.returncode == 0,
                            "returncode": restart.returncode,
                            "stdout_tail": (restart.stdout or "")[-400:],
                            "detail": "schtasks via PowerShell, never Git Bash"})
    ready = ff_cell.wait_for_ready(since=issued)
    record["steps"].append({"step": "ready_marker", "ok": bool(ready),
                            "detail": "ff_cell.wait_for_ready on the REAL %r marker in "
                                      "arc-serve.log" % ff_cell.READY_MARKER})
    if not ready:
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
    print("  gate 4 in_flight        : /slots at 1 Hz + %s delta" % BUSY_SLOTS_SERIES)
    try:
        ref = load_reference(Path(args.reference))
        for key, card in sorted(ref["cards"].items()):
            print("  gate 5 duty_cycle       : %s (%s) reference burst p50 %s W -> threshold %s W"
                  % (key, card.get("adapter"), card.get("burst_p50_w"),
                     round(DUTY_THRESHOLD_FRAC * card["burst_p50_w"], 2)
                     if card.get("burst_p50_w") else None))
        print("                            read from %s, keyed by %s -- never hardcoded"
              % (ref["source"], ref["keyed_by"]))
    except Exception as exc:  # noqa: BLE001
        print("  gate 5 duty_cycle       : frozen reference unreadable (%s) -> duty null" % exc)
    print("  gate 6 depth0_fraction  : session manifest %s -> %s"
          % (ETW_SESSION_MANIFEST, "present" if ETW_SESSION_MANIFEST.is_file()
             else "ABSENT; depth-0 cells carry null and tracing is never started"))
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
            print("  $ powershell -NoProfile -Command \"schtasks /Run /TN ArcServeRestart\"")
            print("  wait: the REAL %r marker, then /health 200 AND a real completion"
                  % ff_cell.READY_MARKER)
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
        if row["status"] in ("REFUSED_GUARD", "STOPPED_AFTER_CELL", "REFUSED_NOT_WARM"):
            print("STOP: %s -- %s" % (row["status"], row["status_reason"]))
            rc = 1
            break
    if args.restore_np:
        print(json.dumps(set_np(BASE_NP, python, args), indent=1, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

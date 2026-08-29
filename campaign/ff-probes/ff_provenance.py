#!/usr/bin/env python3
"""FF-PROVENANCE -- repair the evidentiary record behind ADR-0041 and ADR-0042.

Neither ADR invalidates the campaign's CONCLUSIONS. Both invalidate the PROVENANCE
of the measurements behind them:

  ADR-0042  device order is nondeterministic PER PROCESS LAUNCH, so we do not know
            whether a given receipt was taken against a one-card or a two-card
            incumbent.
  ADR-0041  co-resident work poisons the incumbent until restart, and no rate gate
            existed before 2026-08-29, so we do not know whether the incumbent was
            healthy or poisoned when a co-resident receipt was taken.

This tool does NOT rewrite history. It reads the surviving evidence, reconstructs
what can honestly be reconstructed, and emits a classification ALONGSIDE the
original receipts.

WHAT IT REFUSES TO DO
---------------------
It will not label a receipt INVALID for lack of evidence. `PLACEMENT_CONTEXT_UNKNOWN`
is a distinct and much more common verdict than `PLACEMENT_CONTEXT_INVALID`, and
collapsing the two would repeat exactly the over-claiming that produced three
same-day retractions on 2026-08-29.

DETERMINISM
-----------
Byte-stable for an unchanged tree: no wall clock in the sidecar, all rows sorted.
`--check` regenerates in memory and exits 1 if the committed artifact has drifted --
the same contract as `tools/adr_index.py`.

Read-only against the ledgers unless `--append-summary` is passed, which appends
exactly one summary row per ledger.
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ff_census  # noqa: E402  -- reuse the b70tools parser, never a second copy

HERE = os.path.dirname(os.path.abspath(__file__))
BOUNDARIES = os.path.join(HERE, "provenance-boundaries.json")
TELEMETRY = r"E:\work\battlemage\qwen38-bench-2026-08\results\telemetry"
FF_LEDGER = r"E:\work\battlemage\ff-probes\ff-receipts.jsonl"
LZ_LEDGER = r"E:\work\battlemage\lz-probes\lz-receipts.jsonl"
SIDECAR = r"E:\work\battlemage\ff-probes\provenance-index.jsonl"

# Placement thresholds, in GB of gpu.adapter.vram.local.bytes_committed per B70.
# The 30B-A3B incumbent is ~17.5 GB of weights + 12 GB KV = ~30 GB on one card, or
# ~15/15 split across two. These only have to separate those two cases.
SINGLE_HI_GB = 25.0   # one card carrying essentially the whole model
SINGLE_LO_GB = 1.0    # ...while the other carries nothing
DUAL_MIN_GB = 10.0    # both cards carrying a real share
IDLE_MAX_GB = 0.5     # the activity-window artifact (finding A12)

# ff_ratecheck.py did not exist before this. Every co-resident receipt earlier than
# it was taken with no known-good rate assertion available, healthy or poisoned.
RATE_GATE_EXISTS_FROM = "2026-08-29T06:38:00-07:00"

# A decisive anchor is DIRECT evidence for receipts within this many minutes of it.
# Beyond that the same anchor still applies (same process epoch) but only by inference.
DIRECT_WINDOW_MIN = 15.0

DIR_RE = re.compile(r"adapter-probe-(\d{8})-(\d{6})$")


DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_ts(s):
    """Return (datetime, precision). Receipts carry either a full ISO stamp or a bare date.

    The date-only shape is tested FIRST and explicitly. `datetime.fromisoformat`
    happily parses "2026-08-28" as that day's midnight, which would silently give a
    date-precision receipt a spurious instant -- and midnight on 2026-08-28 falls on
    the far side of that morning's reboot, so 8 LZ receipts were assigned to the
    wrong process epoch and labelled INVALID on the strength of an assumed time.
    Caught in this tool's own first run. Exactly the failure it exists to prevent.
    """
    if not s:
        return None, None
    s = str(s).strip()
    if DATE_ONLY_RE.match(s):
        return datetime.strptime(s, "%Y-%m-%d"), "date"
    try:
        return datetime.fromisoformat(s), "full"
    except ValueError:
        return None, None


def _aware(dt, ref):
    """Give a naive datetime the reference's tzinfo so comparisons are total."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ref.tzinfo if ref and ref.tzinfo else timezone.utc)
    return dt


# --------------------------------------------------------------------------- anchors

def _classify_cards(parsed):
    """Label one per-BDF residency reading as a topology observation."""
    arc = [a for a in parsed.get("adapters") or [] if "Arc" in (a.get("desc") or "")]
    cards = sorted(((a.get("bdf"), a.get("local_committed_gb")) for a in arc),
                   key=lambda kv: kv[0] or "")
    vals = [v for _, v in cards if isinstance(v, (int, float))]

    if len(arc) != 2 or len(vals) != 2:
        return cards, "indeterminate", (
            "expected two Arc B70s with readable counters, saw %d" % len(vals))
    hi, lo = max(vals), min(vals)
    if hi >= SINGLE_HI_GB and lo < SINGLE_LO_GB:
        return cards, "confirmed-single", (
            "one B70 at %.2f GB, the other at %.2f GB -- the whole model on one card"
            % (hi, lo))
    if lo >= DUAL_MIN_GB:
        return cards, "confirmed-dual", (
            "both B70s carrying a real share (%.2f / %.2f GB)" % (lo, hi))
    if hi <= IDLE_MAX_GB:
        return cards, "indeterminate", (
            "both cards read <= %.1f GB (%.2f / %.2f) -- the activity-window artifact "
            "(A12), NOT evidence the cards were empty" % (IDLE_MAX_GB, lo, hi))
    return cards, "indeterminate", (
        "largest allocation %.2f GB is far too small to be the 30B incumbent (~30 GB "
        "single / ~15 GB per card split); likely a probe server" % hi)


def anchor_table(boundaries_doc):
    """Every surviving topology observation, from all three channels.

    Ranked by authority, per ff_census's own hierarchy: llama-server's load report
    first, then b70tools per-BDF residency. Using only the stored b70tools captures
    would have missed both decisive readings that actually cover the campaign window
    -- the -lv 5 load report, and the FF-CENSUS rows already sitting in the ledger.
    """
    anchors = []

    # (1) stored b70tools captures
    for d in sorted(glob.glob(os.path.join(TELEMETRY, "adapter-probe-*"))):
        m = DIR_RE.search(os.path.basename(d))
        path = os.path.join(d, "events.jsonl")
        if not m or not os.path.exists(path):
            continue
        ts = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        parsed = ff_census.adapters_from_events(*ff_census.parse_events(path))
        cards, label, reason = _classify_cards(parsed)
        anchors.append({
            "source": "b70tools-capture", "probe": os.path.basename(d),
            "ts": ts.isoformat(), "label": label, "reason": reason,
            "cards_gb": [{"bdf": b, "local_committed_gb": v} for b, v in cards],
            "temp_spread_c": parsed.get("temp_spread_c"),
            "both_arc_cards_warm": parsed.get("both_arc_cards_warm"),
        })

    # (2) FF-CENSUS rows already in the ledger -- same fields, same thresholds
    for r in read_ledger(FF_LEDGER):
        ad = r.get("adapters")
        if r.get("probe") != "FF-CENSUS" or not isinstance(ad, dict):
            continue
        cards, label, reason = _classify_cards(ad)
        anchors.append({
            "source": "ff-census-row",
            "probe": "FF-CENSUS/%s" % (r.get("run_label") or r.get("phase") or "?"),
            "ts": str(r.get("ts")), "label": label, "reason": reason,
            "cards_gb": [{"bdf": b, "local_committed_gb": v} for b, v in cards],
            "temp_spread_c": ad.get("temp_spread_c"),
            "both_arc_cards_warm": ad.get("both_arc_cards_warm"),
        })

    # (3) curated load-report anchors -- highest authority, free text, so cited not parsed
    for a in boundaries_doc.get("load_report_anchors") or []:
        anchors.append({
            "source": "load-report", "probe": a["source"], "ts": a["ts"],
            "label": a["label"], "reason": a["evidence"],
            "cards_gb": [], "temp_spread_c": None, "both_arc_cards_warm": None,
        })

    anchors.sort(key=lambda a: (a["ts"], a["probe"]))
    return anchors


# --------------------------------------------------------------------------- epochs

def build_epochs(boundaries_doc, anchors):
    """Half-open intervals between evidenced boundaries, each carrying its anchors."""
    bounds = sorted(boundaries_doc["boundaries"], key=lambda b: b["ts"])
    ref = datetime.fromisoformat(bounds[0]["ts"])
    epochs = [{
        "epoch": "E0",
        "start": None,
        "end": bounds[0]["ts"],
        "start_evidence": "no boundary evidence survives before this point",
        "end_evidence": bounds[0]["evidence"],
        "boundary_confidence": bounds[0]["confidence"],
    }]
    for i, b in enumerate(bounds):
        nxt = bounds[i + 1] if i + 1 < len(bounds) else None
        epochs.append({
            "epoch": "E%d" % (i + 1),
            "start": b["ts"],
            "end": nxt["ts"] if nxt else None,
            "start_evidence": b["evidence"],
            "end_evidence": nxt["evidence"] if nxt else "still open when the index was generated",
            "boundary_confidence": b["confidence"],
        })

    for e in epochs:
        s = _aware(datetime.fromisoformat(e["start"]), ref) if e["start"] else None
        en = _aware(datetime.fromisoformat(e["end"]), ref) if e["end"] else None
        mine = []
        for a in anchors:
            at = _aware(datetime.fromisoformat(a["ts"]), ref)
            if (s is None or at >= s) and (en is None or at < en):
                mine.append(a)
        e["anchors"] = [{"probe": a["probe"], "ts": a["ts"], "label": a["label"]} for a in mine]
        decisive = [a for a in mine if a["label"] != "indeterminate"]
        if not decisive:
            e["topology"] = "unknown"
            e["topology_evidence"] = (
                ("%d capture(s) fall in this epoch, none decisive" % len(mine)) if mine
                else "no topology capture falls in this epoch")
        elif len({a["label"] for a in decisive}) > 1:
            e["topology"] = "contradictory"
            e["topology_evidence"] = "decisive anchors disagree: %s" % ", ".join(
                "%s=%s" % (a["probe"], a["label"]) for a in decisive)
        else:
            e["topology"] = decisive[0]["label"]
            e["topology_evidence"] = "; ".join(
                "%s: %s" % (a["probe"], a["reason"]) for a in decisive)
    return epochs


def find_epoch(epochs, dt, ref):
    for e in epochs:
        s = _aware(datetime.fromisoformat(e["start"]), ref) if e["start"] else None
        en = _aware(datetime.fromisoformat(e["end"]), ref) if e["end"] else None
        if (s is None or dt >= s) and (en is None or dt < en):
            return e
    return None


# ------------------------------------------------------------------- classification

def classify(receipt, idx, ledger_tag, epochs, ref, gate_from):
    ts_raw = receipt.get("ts")
    dt, precision = _parse_ts(ts_raw)
    row = {
        "source_ledger": ledger_tag,
        "source_index": idx,
        "ts": ts_raw,
        "probe": receipt.get("probe"),
        "cell": receipt.get("cell") or receipt.get("variant"),
        "coresident": receipt.get("coresident"),
    }

    if dt is None:
        row.update(epoch=None, placement_status="PLACEMENT_CONTEXT_UNKNOWN",
                   placement_reason="receipt carries no parseable timestamp",
                   health_status="INCUMBENT_HEALTH_UNKNOWN",
                   health_reason="cannot be bound to a process epoch",
                   receipt_status="PLACEMENT_CONTEXT_UNKNOWN",
                   receipt_status_reason="receipt carries no parseable timestamp, so it "
                                         "cannot be bound to a process epoch")
        return row

    dt = _aware(dt, ref)
    if precision == "date":
        row.update(epoch=None, placement_status="PLACEMENT_CONTEXT_UNKNOWN",
                   placement_reason="date-precision timestamp spans more than one epoch",
                   health_status="INCUMBENT_HEALTH_UNKNOWN",
                   health_reason="date-precision timestamp spans more than one epoch",
                   receipt_status="PLACEMENT_CONTEXT_UNKNOWN",
                   receipt_status_reason="timestamp has date precision only; the day spans "
                                         "more than one process epoch, so no single "
                                         "topology observation applies")
        return row

    ep = find_epoch(epochs, dt, ref)
    row["epoch"] = ep["epoch"] if ep else None

    # How far the epoch's anchor has to reach to cover this receipt. An anchor is
    # direct evidence at its own instant; reaching it across hours inside an epoch
    # additionally assumes no unrecorded crash-restart (see the boundaries file's
    # blind_spot), so the two cases must not be reported as equally certain.
    reach_min = None
    if ep:
        decisive = [a for a in ep["anchors"] if a["label"] != "indeterminate"]
        gaps = [abs((dt - _aware(datetime.fromisoformat(a["ts"]), ref)).total_seconds()) / 60.0
                for a in decisive]
        reach_min = round(min(gaps), 1) if gaps else None
    row["placement_reach_minutes"] = reach_min
    row["placement_confidence"] = (
        None if reach_min is None else
        ("direct" if reach_min <= DIRECT_WINDOW_MIN else "epoch-inferred"))
    reach_note = ("" if reach_min is None else
                  " Nearest decisive anchor is %.1f min away; reaching it across the epoch "
                  "assumes no unrecorded crash-restart (ArcServeBoot RestartCount 3 @ PT1M "
                  "leaves no trace in any surviving channel)." % reach_min)

    # --- placement dimension
    topo = ep["topology"] if ep else "unknown"
    if topo == "confirmed-single":
        row["placement_status"] = "PLACEMENT_CONTEXT_INVALID"
        row["placement_reason"] = (
            "epoch %s is anchored SINGLE-CARD (%s). Any reading of this receipt that "
            "assumed two working B70s is measuring a different machine than it claims.%s"
            % (ep["epoch"], ep["topology_evidence"], reach_note))
    elif topo == "confirmed-dual":
        row["placement_status"] = "PLACEMENT_CONTEXT_CONFIRMED"
        row["placement_reason"] = "epoch %s is anchored DUAL-CARD (%s).%s" % (
            ep["epoch"], ep["topology_evidence"], reach_note)
    elif topo == "contradictory":
        row["placement_status"] = "PLACEMENT_CONTEXT_UNKNOWN"
        row["placement_reason"] = "epoch %s carries contradictory anchors (%s)" % (
            ep["epoch"], ep["topology_evidence"])
    else:
        row["placement_status"] = "PLACEMENT_CONTEXT_UNKNOWN"
        row["placement_reason"] = (
            "no decisive topology anchor falls in epoch %s (%s). Per ADR-0042 a topology "
            "observation does not survive a process restart, so anchors from adjacent "
            "epochs do not transfer. Absence of evidence, not evidence of a defect."
            % (row["epoch"], ep["topology_evidence"] if ep else "no epoch matched"))

    # --- health dimension
    if receipt.get("coresident") is True and dt < gate_from:
        row["health_status"] = "INCUMBENT_HEALTH_UNKNOWN"
        row["health_reason"] = (
            "co-resident receipt taken before ff_ratecheck.py existed (%s). Per ADR-0041 "
            "the incumbent may have been poisoned to ~0.27x by earlier co-resident work "
            "with every health check still passing. The probe-side number is likely sound "
            "-- a fresh server keeps its rate beside a poisoned incumbent -- but any "
            "CO-RESIDENCY INTERPRETATION drawn from it is not." % gate_from.isoformat())
    elif receipt.get("coresident") is True:
        row["health_status"] = "INCUMBENT_HEALTH_GATED"
        row["health_reason"] = "co-resident receipt taken after the rate gate existed"
    else:
        row["health_status"] = "NOT_APPLICABLE"
        row["health_reason"] = "receipt is not marked co-resident"

    if row["placement_status"] in ("PLACEMENT_CONTEXT_INVALID", "PLACEMENT_CONTEXT_UNKNOWN"):
        row["receipt_status"] = row["placement_status"]
        row["receipt_status_reason"] = row["placement_reason"]
    elif row["health_status"] == "INCUMBENT_HEALTH_UNKNOWN":
        row["receipt_status"] = "INCUMBENT_HEALTH_UNKNOWN"
        row["receipt_status_reason"] = row["health_reason"]
    else:
        row["receipt_status"] = "OK"
        row["receipt_status_reason"] = "placement confirmed for the epoch; health gated or n/a"
    return row


def read_ledger(path):
    rows = []
    if not os.path.exists(path):
        return rows
    for line in io.open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({})
    return rows


def build(epochs, boundaries_doc):
    ref = datetime.fromisoformat(boundaries_doc["boundaries"][0]["ts"])
    gate_from = _aware(datetime.fromisoformat(RATE_GATE_EXISTS_FROM), ref)
    out = []
    for tag, path in (("FF", FF_LEDGER), ("LZ", LZ_LEDGER)):
        for i, r in enumerate(read_ledger(path)):
            if r.get("probe") in ("FF-PROVENANCE", "LZ-PROVENANCE"):
                continue  # never classify our own summary rows
            out.append(classify(r, i, tag, epochs, ref, gate_from))
    out.sort(key=lambda r: (r["source_ledger"], str(r["ts"]), r["source_index"]))
    return out


def render(anchors, epochs, rows):
    """The byte-stable artifact. No wall clock anywhere in here, by contract."""
    buf = io.StringIO()
    header = {"kind": "provenance-index-header", "contract_version": "ff-provenance.v1",
              "anchors": anchors, "epochs": epochs,
              "thresholds_gb": {"single_hi": SINGLE_HI_GB, "single_lo": SINGLE_LO_GB,
                                "dual_min": DUAL_MIN_GB, "idle_max": IDLE_MAX_GB},
              "rate_gate_exists_from": RATE_GATE_EXISTS_FROM}
    buf.write(json.dumps(header, ensure_ascii=False, sort_keys=True) + "\n")
    for r in rows:
        buf.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    return buf.getvalue()


def counts(rows):
    tally = {}
    for r in rows:
        for dim in ("receipt_status", "placement_status", "health_status"):
            tally.setdefault(dim, {})
            tally[dim][r.get(dim)] = tally[dim].get(r.get(dim), 0) + 1
    return tally


def main() -> int:
    ap = argparse.ArgumentParser(description="Classify FF/LZ receipts by provenance")
    ap.add_argument("--check", action="store_true",
                    help="regenerate in memory and exit 1 if the sidecar has drifted")
    ap.add_argument("--append-summary", action="store_true",
                    help="append one summary row to each ledger (append-only, never rewrites)")
    args = ap.parse_args()

    boundaries_doc = json.load(io.open(BOUNDARIES, encoding="utf-8"))
    anchors = anchor_table(boundaries_doc)
    epochs = build_epochs(boundaries_doc, anchors)
    rows = build(epochs, boundaries_doc)
    text = render(anchors, epochs, rows)

    if args.check:
        if not os.path.exists(SIDECAR):
            print("DRIFT: %s does not exist" % SIDECAR)
            return 1
        current = io.open(SIDECAR, encoding="utf-8", newline="").read()
        if current != text:
            print("DRIFT: %s differs from a fresh generation" % SIDECAR)
            return 1
        print("ff_provenance --check: clean (%d rows)" % len(rows))
        return 0

    os.makedirs(os.path.dirname(SIDECAR), exist_ok=True)
    with io.open(SIDECAR, "w", encoding="utf-8", newline="") as f:
        f.write(text)

    tally = counts(rows)
    print("=== FF-PROVENANCE ===")
    print("  anchors: %d captures" % len(anchors))
    for a in anchors:
        if a["label"] != "indeterminate":
            print("     %-25s %-16s %-16s <- %s"
                  % (a["ts"], a["label"], a["source"], a["probe"]))
    n_ind = sum(1 for a in anchors if a["label"] == "indeterminate")
    print("     %d indeterminate (activity-window artifact -- not evidence of absence)" % n_ind)
    print("  epochs: %d" % len(epochs))
    for e in epochs:
        print("     %-3s %-26s -> %-26s topology=%s"
              % (e["epoch"], e["start"] or "(unbounded)", e["end"] or "(open)", e["topology"]))
    print("  receipts classified: %d" % len(rows))
    for dim in ("receipt_status", "placement_status", "health_status"):
        print("    %s:" % dim)
        for k, v in sorted(tally[dim].items(), key=lambda kv: -kv[1]):
            print("       %5d  %s" % (v, k))
    print("  -> %s" % SIDECAR)

    if args.append_summary:
        ts = datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()
        for tag, path in (("FF", FF_LEDGER), ("LZ", LZ_LEDGER)):
            mine = [r for r in rows if r["source_ledger"] == tag]
            row = {"ts": ts, "probe": "%s-PROVENANCE" % tag,
                   "result": "receipts classified against ADR-0041/0042; no history rewritten",
                   "coresident": False,
                   "classified_rows": len(mine),
                   "counts": counts(mine),
                   "anchors": anchors,
                   "epochs": [{k: e[k] for k in ("epoch", "start", "end", "topology")}
                              for e in epochs],
                   "sidecar": SIDECAR,
                   "regenerate_with": "python campaign/ff-probes/ff_provenance.py",
                   "note": ("Per-receipt rows live in the sidecar, which is regenerable and "
                            "byte-stable. This ledger row is the summary; the originals are "
                            "untouched.")}
            with io.open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print("  -> appended %s-PROVENANCE summary to %s" % (tag, path))
    return 0


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.workflow.project_capacity import (
    KNOWN_BAD_MIN_FAILURES,
    KNOWN_GOOD_MIN_SUCCESS_RATE,
    _combo_key,
    _confidence,
    collect_event_files,
    extract_observations,
)
from tools.workflow.project_associations import evidence_watermark
from tools.workflow.project_state import read_events

# Traced constants (D18): arbitrary-at-birth, revision path is to change here + append Dx note.
CONFIDENCE_BUCKETS = (
    # (lo_inclusive, hi_exclusive) — score = samples/(samples+2)
    (0.0, 0.2),
    (0.2, 0.4),
    (0.4, 0.6),
    (0.6, 0.8),
    (0.8, 1.0),
)
# Minimum predictions per bucket before we report a rate; below this we say "insufficient_evidence"
# rather than extrapolating from noise.
CALIBRATION_MIN_BUCKET_N = 5
# Minimum total scored predictions in the corpus before the calibration candidate is worth proposing.
CALIBRATION_MIN_TOTAL_PREDICTIONS = 10
# Conventional artifact home for the calibration report (an artifact directory, NOT knowledge/).
CALIBRATION_CONVENTIONAL_PATH = "runs/calibration/calibration_report.json"


def _prior_state(observations_before: list[dict], combo_key: str) -> dict | None:
    """Compute the finding type and confidence_score for combo_key from a prefix of observations.
    Returns None if the combo has never been seen."""
    samples = successes = failures = 0
    for obs in observations_before:
        if _combo_key(obs) == combo_key:
            samples += 1
            if obs.get("outcome") == "success":
                successes += 1
            else:
                failures += 1
    if samples == 0:
        return None
    success_rate = successes / samples
    confidence_score = _confidence(samples)
    if successes >= 1 and success_rate >= KNOWN_GOOD_MIN_SUCCESS_RATE:
        finding_type = "known_good"
    elif successes == 0 and failures >= KNOWN_BAD_MIN_FAILURES:
        finding_type = "known_bad"
    else:
        finding_type = "uncertain"
    return {"finding_type": finding_type, "confidence_score": confidence_score, "samples": samples}


def calibrate(observations: list[dict]) -> dict:
    """Walk the corpus in evidence-time order. At each observation, compute the finding +
    confidence_score that existed for that subject BEFORE this observation, then check whether
    the actual outcome matched the prediction. Bucket by prior confidence_score and report
    per-bucket calibration statistics.

    The evidence_watermark embedded in the output is the freshness marker for staleness checks;
    it is never a wall-clock timestamp (D18)."""
    # Deterministic sort: timestamp primary, observation_id secondary (stable tiebreaker).
    sorted_obs = sorted(
        observations,
        key=lambda o: (o.get("timestamp") or "", o.get("observation_id") or ""),
    )
    watermark = evidence_watermark(observations)

    # Running per-combo counters to avoid O(n²) rescanning the prefix on each step.
    combo_counts: dict[str, dict] = {}

    predictions: list[dict] = []
    excluded: dict[str, int] = {}

    for obs in sorted_obs:
        key = _combo_key(obs)
        prior = combo_counts.get(key)

        if prior is None:
            # This combo has never been seen before this observation.
            excluded["no_prior_finding"] = excluded.get("no_prior_finding", 0) + 1
        else:
            s, succ, fail = prior["samples"], prior["successes"], prior["failures"]
            success_rate = succ / s
            confidence_score = _confidence(s)

            if succ >= 1 and success_rate >= KNOWN_GOOD_MIN_SUCCESS_RATE:
                finding_type = "known_good"
            elif succ == 0 and fail >= KNOWN_BAD_MIN_FAILURES:
                finding_type = "known_bad"
            else:
                finding_type = "uncertain"

            if finding_type in ("known_good", "known_bad"):
                predicted_success = finding_type == "known_good"
                actual_success = obs.get("outcome") == "success"
                predictions.append({
                    "observation_id": obs.get("observation_id"),
                    "combo_key": key,
                    "finding_type": finding_type,
                    "confidence_score": confidence_score,
                    "matched": predicted_success == actual_success,
                })
            else:
                excluded[finding_type] = excluded.get(finding_type, 0) + 1

        # Update running counter AFTER reading prior state.
        if key not in combo_counts:
            combo_counts[key] = {"samples": 0, "successes": 0, "failures": 0}
        combo_counts[key]["samples"] += 1
        if obs.get("outcome") == "success":
            combo_counts[key]["successes"] += 1
        else:
            combo_counts[key]["failures"] += 1

    # Bucket predictions by prior confidence_score.
    buckets = []
    for lo, hi in CONFIDENCE_BUCKETS:
        bucket_preds = [p for p in predictions if lo <= p["confidence_score"] < hi]
        n = len(bucket_preds)
        label = f"[{lo},{hi})"
        if n < CALIBRATION_MIN_BUCKET_N:
            buckets.append({
                "bucket": label,
                "lo": lo,
                "hi": hi,
                "n": n,
                "status": "insufficient_evidence",
                "observed_rate": None,
                "curve_implied_rate": None,
                "bias": None,
            })
        else:
            observed_rate = round(sum(1 for p in bucket_preds if p["matched"]) / n, 4)
            # curve_implied_rate is the MEAN confidence_score of the predictions in this bucket,
            # not the bucket midpoint — the mean reflects where the actual scores landed.
            curve_implied_rate = round(sum(p["confidence_score"] for p in bucket_preds) / n, 4)
            bias = round(observed_rate - curve_implied_rate, 4)
            buckets.append({
                "bucket": label,
                "lo": lo,
                "hi": hi,
                "n": n,
                "status": "ok",
                "observed_rate": observed_rate,
                "curve_implied_rate": curve_implied_rate,
                "bias": bias,
            })

    return {
        "contract_version": "calibration-report.v1",
        "evidence_watermark": watermark,
        "total_observations": len(observations),
        "total_scored_predictions": len(predictions),
        "total_excluded": sum(excluded.values()),
        "excluded_by_reason": excluded,
        "buckets": buckets,
    }


def count_scored_predictions(observations: list[dict]) -> int:
    """Count observations that have a prior known_good or known_bad finding.
    Used by the experiment candidate synthesizer to gate the calibration proposal."""
    return calibrate(observations)["total_scored_predictions"]


def materialize_calibration(event_files: list[Path], out_path: Path) -> dict:
    observations: list[dict] = []
    for event_file in event_files:
        extracted, _ = extract_observations(read_events(event_file), event_file)
        observations.extend(extracted)

    report = calibrate(observations)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Calibrate the confidence curve samples/(samples+2) against actual corpus outcomes."
    )
    parser.add_argument(
        "sources",
        nargs="+",
        help="events.jsonl files or directories to scan recursively for them",
    )
    parser.add_argument(
        "--out",
        required=True,
        metavar="FILE",
        help=f"Artifact output path (conventional: {CALIBRATION_CONVENTIONAL_PATH})",
    )
    args = parser.parse_args(argv)

    event_files = collect_event_files([Path(raw) for raw in args.sources])
    report = materialize_calibration(event_files, Path(args.out))

    summary: dict = {
        "event_files": len(event_files),
        "total_observations": report["total_observations"],
        "total_scored_predictions": report["total_scored_predictions"],
        "evidence_watermark": report["evidence_watermark"],
        "buckets": [
            {
                "bucket": b["bucket"],
                "n": b["n"],
                "status": b["status"],
                "bias": b["bias"],
            }
            for b in report["buckets"]
        ],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

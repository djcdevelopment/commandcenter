"""
Standalone GPU physical telemetry collector.

CLI:
    python -m tools.telemetry.collect_physical \
        --wrap "<command>" --out <file.json> \
        [--interval-s 2] \
        [--source nvidia|mock:<samples.json>]

Runs the wrapped command, samples GPU telemetry while it runs (or uses a
mock series in full), writes {"hardware_profile_id": ..., "physical": {...}}
to --out, then exits with the wrapped command's exit code.

Emitted physical keys are a strict subset of the capacity-observation.v1
observed.physical properties. model_residency is DERIVED by projection and
is never emitted here; model_* raw fields are always emitted as null (this
collector cannot observe them).
"""

import argparse
import json
import subprocess
import sys
import threading


# ---------------------------------------------------------------------------
# Pure summary math (independently unit-tested)
# ---------------------------------------------------------------------------

def summarize(temp_series, power_series, clock_series):
    """Compute physical summary dict from raw per-sample lists.

    sustained_avg is the mean of the back ceil(n/2) samples, i.e.
    series[n//2:].  This is the "back half" statistic that feeds the
    sustained-load-decay finding (schema description on gpu_temp_c_sustained_avg).

    fan_rpm_avg is always null: nvidia-smi reports fan.speed as a percentage of
    max fan speed, not RPM.  Emitting null is the honest choice; see BUILD-NOTES-C2.md.
    model_* fields are always null: this collector cannot determine model residency.

    Empty series yields null for all numeric fields.
    """
    def _mean(lst):
        return sum(lst) / len(lst) if lst else None

    def _back_half(lst):
        # ceil(n/2) tail samples = lst[n//2:]
        return lst[len(lst) // 2:] if lst else []

    return {
        "gpu_temp_c_peak":          max(temp_series) if temp_series else None,
        "gpu_temp_c_sustained_avg": _mean(_back_half(temp_series)),
        "power_w_avg":              _mean(power_series),
        "power_w_peak":             max(power_series) if power_series else None,
        "fan_rpm_avg":              None,
        "clock_mhz_avg":            _mean(clock_series),
        # model_* raw sensor facts: this collector cannot determine them
        "model_loaded_at_start":    None,
        "model_load_count":         None,
        "model_unload_count":       None,
        "model_load_s":             None,
        # model_residency is DERIVED by projection — never set here
    }


# ---------------------------------------------------------------------------
# Hardware profile ID normalization
# ---------------------------------------------------------------------------

def _normalize_profile_id(name, driver_version):
    """Return "<name>|<driver_version>" lowercased with spaces replaced by "-"."""
    return f"{name}|{driver_version}".lower().replace(" ", "-")


# ---------------------------------------------------------------------------
# nvidia-smi source
# ---------------------------------------------------------------------------

_NVIDIA_QUERY = "name,driver_version,temperature.gpu,power.draw,fan.speed,clocks.sm"


def _parse_float(text):
    """Parse one numeric CSV column; unparseable (e.g. "[N/A]") → None."""
    try:
        return float(text)
    except ValueError:
        return None


def _query_nvidia():
    """Run nvidia-smi once; return a sample dict or None on any error."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                f"--query-gpu={_NVIDIA_QUERY}",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        line = result.stdout.strip().split("\n")[0]
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            return None
        name, driver_version = parts[0], parts[1]
        # Parse each numeric column INDIVIDUALLY: an unparseable value (e.g.
        # "[N/A]" for fan.speed on some GPUs) nulls THAT field only and keeps
        # the sample.  fan.speed (parts[4]) is deliberately not parsed at all:
        # it is a percent, not RPM, and fan_rpm_avg is always emitted null
        # (see BUILD-NOTES-C2.md extension point).
        temp_c    = _parse_float(parts[2])
        power_w   = _parse_float(parts[3])
        clock_mhz = _parse_float(parts[5])
        # Discard the sample only when it carries no information at all:
        # name/driver empty AND every numeric field unparseable.
        if (not name and not driver_version
                and temp_c is None and power_w is None and clock_mhz is None):
            return None
        return {
            "name":           name,
            "driver_version": driver_version,
            "temp_c":         temp_c,
            "power_w":        power_w,
            "clock_mhz":      clock_mhz,
        }
    except (FileNotFoundError, subprocess.TimeoutExpired, IndexError):
        return None


def collect_nvidia(interval_s, stop_event):
    """Sample loop for the nvidia source.

    Returns (hardware_profile_id, temp_series, power_series, clock_series).
    If nvidia-smi is unavailable on the first attempt a warning is printed to
    stderr; subsequent failures silently skip that sample.  An empty series
    means summarize() will produce all-null physical fields.
    """
    temp_series, power_series, clock_series = [], [], []
    hardware_profile_id = None
    warned = False

    while not stop_event.is_set():
        sample = _query_nvidia()
        if sample is None:
            if not warned:
                print(
                    "WARNING: nvidia-smi unavailable or returned an error — "
                    "physical telemetry will be all null",
                    file=sys.stderr,
                    flush=True,
                )
                warned = True
        else:
            if hardware_profile_id is None:
                hardware_profile_id = _normalize_profile_id(
                    sample["name"], sample["driver_version"]
                )
            # Per-field nulls (unparseable columns) are skipped per series;
            # the rest of the sample still contributes.
            if sample["temp_c"] is not None:
                temp_series.append(sample["temp_c"])
            if sample["power_w"] is not None:
                power_series.append(sample["power_w"])
            if sample["clock_mhz"] is not None:
                clock_series.append(sample["clock_mhz"])
        stop_event.wait(interval_s)

    return hardware_profile_id, temp_series, power_series, clock_series


# ---------------------------------------------------------------------------
# mock source
# ---------------------------------------------------------------------------

def collect_mock(source_path):
    """Load the entire mock sample file as the series, independent of timing.

    Each row: {"name", "driver_version", "temp_c", "power_w", "fan_pct", "clock_mhz"}.
    Returns (hardware_profile_id, temp_series, power_series, clock_series).
    """
    with open(source_path, encoding="utf-8") as f:
        samples = json.load(f)

    if not samples:
        return None, [], [], []

    hardware_profile_id = _normalize_profile_id(
        samples[0]["name"], samples[0]["driver_version"]
    )
    return (
        hardware_profile_id,
        [s["temp_c"]   for s in samples],
        [s["power_w"]  for s in samples],
        [s["clock_mhz"] for s in samples],
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Wrap a command and collect GPU physical telemetry."
    )
    parser.add_argument("--wrap",       required=True, help="Shell command to run")
    parser.add_argument("--out",        required=True, help="Output JSON path")
    parser.add_argument("--interval-s", type=float, default=2.0,
                        help="nvidia sample interval in seconds (default: 2)")
    parser.add_argument("--source",     default="nvidia",
                        help='"nvidia" or "mock:<path>" (default: nvidia)')
    args = parser.parse_args()

    use_mock = args.source.startswith("mock:")

    if use_mock:
        mock_path = args.source[len("mock:"):]
        hardware_profile_id, temp_s, power_s, clock_s = collect_mock(mock_path)
        proc = subprocess.run(args.wrap, shell=True)
        exit_code = proc.returncode
    else:
        # nvidia: sample in a background thread while the command runs
        stop_event = threading.Event()
        result_box = [None]

        def _sample_thread():
            result_box[0] = collect_nvidia(args.interval_s, stop_event)

        t = threading.Thread(target=_sample_thread, daemon=True)
        t.start()

        proc = subprocess.run(args.wrap, shell=True)
        exit_code = proc.returncode

        stop_event.set()
        t.join(timeout=15)

        result = result_box[0] or (None, [], [], [])
        hardware_profile_id, temp_s, power_s, clock_s = result

    physical = summarize(temp_s, power_s, clock_s)

    output = {
        "hardware_profile_id": hardware_profile_id,
        "physical":            physical,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
        f.write("\n")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()

import argparse
import json
import os
import subprocess
import sys
import time
from typing import List, Dict, Any, Optional


def parse_nvidia_smi_output(line: str) -> Optional[Dict[str, Any]]:
    """Parse a single line of nvidia-smi output into a dict. Returns None if parsing fails."""
    parts = line.strip().split(',')
    if len(parts) != 6:
        return None
    try:
        name, driver_version, temp_c, power_w, fan_pct, clock_mhz = parts
        return {
            "name": name.strip(),
            "driver_version": driver_version.strip(),
            "temp_c": float(temp_c),
            "power_w": float(power_w),
            "fan_pct": float(fan_pct),
            "clock_mhz": float(clock_mhz)
        }
    except (ValueError, TypeError):
        return None

def compute_summary(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute physical summary from a list of samples. Returns a dict with nulls for empty series."""
    if not samples:
        return {
            "gpu_temp_c_peak": None,
            "gpu_temp_c_sustained_avg": None,
            "power_w_avg": None,
            "power_w_peak": None,
            "fan_rpm_avg": None,
            "clock_mhz_avg": None
        }

    temps = [s["temp_c"] for s in samples]
    powers = [s["power_w"] for s in samples]
    clocks = [s["clock_mhz"] for s in samples]

    # Peak values
    gpu_temp_c_peak = max(temps)
    power_w_peak = max(powers)

    # Average values
    gpu_temp_c_avg = sum(temps) / len(temps)
    power_w_avg = sum(powers) / len(powers)
    clock_mhz_avg = sum(clocks) / len(clocks)

    # Sustained average: mean of the second half of the series
    n = len(temps)
    half = (n + 1) // 2  # ceil(n/2)
    sustained_temps = temps[-half:]
    gpu_temp_c_sustained_avg = sum(sustained_temps) / len(sustained_temps)

    return {
        "gpu_temp_c_peak": gpu_temp_c_peak,
        "gpu_temp_c_sustained_avg": gpu_temp_c_sustained_avg,
        "power_w_avg": power_w_avg,
        "power_w_peak": power_w_peak,
        "fan_rpm_avg": None,  # Always null for nvidia source due to unit mismatch
        "clock_mhz_avg": clock_mhz_avg
    }

def get_hardware_profile_id(samples: List[Dict[str, Any]]) -> Optional[str]:
    """Extract hardware profile ID from first sample, normalized."""
    if not samples:
        return None
    name = samples[0]["name"].lower().replace(' ', '-')
    driver_version = samples[0]["driver_version"]
    return f"{name}|{driver_version}"

def collect_nvidia_samples(interval_s: int = 2) -> List[Dict[str, Any]]:
    """Collect samples using nvidia-smi. Returns list of parsed samples or empty list on error."""
    samples = []
    try:
        while True:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,driver_version,temperature.gpu,power.draw,fan.speed,clocks.sm", 
                 "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                print(f"nvidia-smi failed with exit code {result.returncode}. Using null values.", file=sys.stderr)
                return []
            line = result.stdout.strip()
            if not line:
                continue
            parsed = parse_nvidia_smi_output(line)
            if parsed is None:
                continue
            samples.append(parsed)
            time.sleep(interval_s)
    except subprocess.TimeoutExpired:
        pass
    except KeyboardInterrupt:
        pass
    return samples

def collect_mock_samples(mock_file: str) -> List[Dict[str, Any]]:
    """Read mock samples from a JSON file."""
    try:
        with open(mock_file, 'r') as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("Mock data must be a list of samples")
        return data
    except Exception as e:
        print(f"Failed to read mock file {mock_file}: {e}", file=sys.stderr)
        return []

def main():
    parser = argparse.ArgumentParser(description="Collect physical telemetry from GPU runs")
    parser.add_argument("--wrap", required=True, help="Command to wrap")
    parser.add_argument("--out", required=True, help="Output file path")
    parser.add_argument("--interval-s", type=int, default=2, help="Sampling interval in seconds (default: 2)")
    parser.add_argument("--source", required=True, help="Source: nvidia or mock:<path>")

    args = parser.parse_args()

    # Determine source
    if args.source.startswith("mock:"):
        mock_path = args.source[5:]
        samples = collect_mock_samples(mock_path)
    elif args.source == "nvidia":
        samples = collect_nvidia_samples(args.interval_s)
    else:
        print(f"Unknown source: {args.source}. Use 'nvidia' or 'mock:<path>'", file=sys.stderr)
        sys.exit(1)

    # Compute summary
    summary = compute_summary(samples)
    hardware_profile_id = get_hardware_profile_id(samples)

    # Build output
    output = {
        "hardware_profile_id": hardware_profile_id,
        "physical": summary
    }

    # Write output
    try:
        with open(args.out, 'w') as f:
            json.dump(output, f, indent=2)
    except Exception as e:
        print(f"Failed to write output to {args.out}: {e}", file=sys.stderr)
        sys.exit(1)

    # Run wrapped command
    try:
        cmd = args.wrap
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        sys.exit(result.returncode)
    except Exception as e:
        print(f"Failed to run wrapped command '{cmd}': {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""Watch both AM4 GPU cores every 20 seconds; one sample >=90 C stops the lap."""
import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import time
import signal

from tools.ops.bench27_guard import durable, guard_failure, utc, door_action, pending_jobs, stop_campaign


def sample():
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '10.44.0.2',
                             'nvidia-smi --query-gpu=pci.bus_id,temperature.gpu,power.draw,memory.used --format=csv,noheader,nounits'],
                            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=8, check=True)
    return parse_samples(result.stdout)


def parse_samples(text):
    rows = []
    for line in text.splitlines():
        pci, temp, power, memory = [field.strip() for field in line.split(',')]
        rows.append({'pci': pci.lower(), 'gpu_core_c': float(temp), 'power_w': float(power), 'memory_mib': float(memory)})
    if {r['pci'][-10:] for r in rows} != {'00:09:00.0', '00:0a:00.0'} or len(rows) != 2:
        raise ValueError('AM4 GPU identities do not match the recorded pair')
    if any(not 0 <= r['gpu_core_c'] <= 120 for r in rows):
        raise ValueError('invalid core telemetry')
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('out', type=Path)
    ap.add_argument('--on-trip', required=True, help='idempotent command to stop the owned AM4 lap')
    a = ap.parse_args()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path.home() / '.cache/bench27-am4-guard.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    trip = Path(str(a.out) + '.tripped')
    projection = Path.home() / 'hearth-production/var/execution/projection.sqlite'
    def stop_signal(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, stop_signal)
    try:
        door_action('readiness', 'codex')
        pending_jobs(projection, 'am4-vllm', 'codex-cli')
        while True:
            start = time.monotonic()
            row = {'utc': utc()}
            try:
                row['gpus'] = sample()
                if any(gpu['gpu_core_c'] >= 90 for gpu in row['gpus']) and not trip.exists():
                    durable(trip, {'utc': utc(), 'reason': 'AM4 GPU core >=90 C', 'gpus': row['gpus']})
            except Exception as exc:
                row['telemetry_error'] = type(exc).__name__
                if not trip.exists():
                    durable(trip, {'utc': utc(), 'reason': 'AM4 telemetry unavailable', 'error': type(exc).__name__})
            durable(a.out, row, append=True)
            if trip.exists():
                action = {'utc': utc(), 'stop': stop_campaign(a.on_trip, {'card': 'am4', 'backend': 'am4-vllm'})}
                try:
                    jobs = pending_jobs(projection, 'am4-vllm', 'codex-cli')
                    action['cancelled'] = door_action('cancel', 'codex', jobs, 'AM4 thermal guard tripped') if jobs else []
                except Exception as exc:
                    action['cancel_error'] = type(exc).__name__
                durable(Path(str(a.out) + '.actions.jsonl'), action, append=True)
            time.sleep(max(0, 20 - (time.monotonic() - start)))
    except BaseException as exc:
        try:
            guard_failure(a.out, exc)
        finally:
            stop_campaign(a.on_trip, {'card': 'am4', 'backend': 'am4-vllm'})
            try:
                jobs = pending_jobs(projection, 'am4-vllm', 'codex-cli')
                if jobs:
                    door_action('cancel', 'codex', jobs, 'AM4 guard shutdown; telemetry unavailable')
            except Exception:
                pass  # Sticky marker and lap BindsTo guard stop new and in-flight work.
        raise
    finally:
        lock.close()


if __name__ == '__main__':
    main()

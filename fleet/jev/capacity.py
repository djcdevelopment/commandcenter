"""OMEN-side advisory observations; never starts models or admits placements.

Run with the fleet Python: ``python -m fleet.jev.capacity [--json]``.
Probe results have independent acquisition windows. They are not reservations.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from fleet.jev.client import atomic_json
from fleet.jev.local import ROOT, STATE

B70TOOLS = Path('E:/work/b70tools/build/b70tools.exe')
AM4 = ['ssh', '-b', '10.44.0.1', '-o', 'BatchMode=yes', '-o',
       'ConnectTimeout=5', '-o', 'HostName=10.44.0.2', '-o',
       'HostKeyAlias=am4', '-o', 'StrictHostKeyChecking=yes', 'am4']


def now():
    return datetime.now(timezone.utc).isoformat()


def command(argv, timeout):
    result = subprocess.run(argv, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=timeout,
                            cwd=ROOT)
    if result.returncode:
        raise RuntimeError('probe_command_failed')
    if len(result.stdout) > 2_000_000:
        raise ValueError('probe_output_too_large')
    return result.stdout


def worker():
    # Existing entry point privately loads its own credentials; none reach stdout.
    return json.loads(command([sys.executable, str(ROOT / 'fleet/hermes/serve-hearth.py'),
                               '--probe-worker'], 30))


def nvidia():
    raw = command(AM4 + ['nvidia-smi --query-gpu=name,uuid,memory.total,memory.used,'
                         'memory.free,utilization.gpu --format=csv,noheader,nounits'], 12)
    rows = []
    for fields in csv.reader(io.StringIO(raw), skipinitialspace=True):
        if len(fields) != 6:
            raise ValueError('unexpected_nvidia_columns')
        row = {'name': fields[0], 'uuid': fields[1]}
        for key, value in zip(('total_mib', 'used_mib', 'free_mib', 'utilization_pct'), fields[2:]):
            # Keep unavailable fields unknown; do not derive driver-free memory.
            row[key] = int(value) if value.isascii() and value.isdigit() else None
        rows.append(row)
    if not rows:
        raise ValueError('no_nvidia_adapters')
    return {'source': 'nvidia-smi', 'scope': 'adapter', 'adapters': rows,
            'allocation_permission': False}


def b70():
    from fleet.jev.b70_capacity import parse_b70
    root = STATE / 'capacity'
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='b70-', dir=root))
    command([str(B70TOOLS), 'run', '--ticks', '3', '--out', str(directory)], 12)
    source = directory / 'events.jsonl'
    if source.stat().st_size > 2_000_000:
        raise ValueError('observer_recording_too_large')
    data = source.read_bytes()
    records = [json.loads(line) for line in data.decode('utf-8').splitlines() if line.strip()]
    adapters = parse_b70(records)
    if not adapters:
        raise ValueError('no_b70_identities')
    return {'source': 'b70tools', 'adapters': adapters,
            'raw_path': str(source), 'raw_sha256': hashlib.sha256(data).hexdigest(),
            'field_sources': {
                'total_bytes': 'DXGI identity: physical dedicated capacity, not available memory',
                'adapter_local_committed_bytes': 'PDH_AdapterMemory: adapter-wide committed',
                'adapter_non_local_committed_bytes': 'PDH_AdapterMemory: adapter-wide non-local committed',
                'observer_process_local_bytes': 'DXGI_VideoMemoryInfo: observer process only',
                'free_bytes': 'unknown: no qualified SYCL-aware free-memory measurement'},
            'warnings': ['PDH has historically undercounted SYCL allocations.',
                         'Process budgets/usage are not adapter-free memory.',
                         'Three ticks include only one slow memory sample; no trend or spill verdict.'],
            'allocation_permission': False}


def observe(probe):
    started = now()
    try:
        result = {'available': True, 'data': probe()}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        # Exception text may contain command arguments. Emit only the class.
        result = {'available': False, 'data': None, 'error_kind': type(error).__name__}
    return {'observed_from': started, 'observed_until': now(), 'ttl_s': 30, **result}


def capture():
    probes = {'omen_worker': worker, 'omen_b70': b70, 'am4_nvidia': nvidia}
    with ThreadPoolExecutor(max_workers=len(probes)) as pool:
        futures = {name: pool.submit(observe, probe) for name, probe in probes.items()}
        observations = {name: future.result() for name, future in futures.items()}
    return {'schema': 'fleet-capacity-observation.v1', 'captured_at': now(),
            'advisory_only': True, 'new_model_placement_authorized': False,
            'observations': observations}


def format_report(value):
    lines = ['Fleet capacity observations (advisory; not a reservation)',
             'Acquired: ' + value['captured_at']]
    for name, item in value['observations'].items():
        lines.append(f"{name}: {'observed' if item['available'] else 'unknown'}; "
                     f"{item['observed_from']} .. {item['observed_until']} (TTL 30s)")
        if not item['available']:
            continue
        data = item['data']
        if name == 'omen_worker':
            lines.append(f"  {data.get('model')}: ready={data.get('ready')}, "
                         f"free slots={data.get('free_slots')}/{data.get('parallel_slots')}, "
                         f"context/slot={data.get('context_length')}")
        elif name == 'am4_nvidia':
            for row in data['adapters']:
                lines.append(f"  {row['name']} ({row['uuid']}): "
                             f"used={row['used_mib']} MiB, driver-free={row['free_mib']} MiB")
        else:
            for row in data['adapters']:
                lines.append(f"  {row['bdf']}: adapter committed={row['adapter_local_committed_bytes']} B, "
                             f"non-local={row['adapter_non_local_committed_bytes']} B; "
                             f"observer process={row['observer_process_local_bytes']} B; free=unknown")
                lines.append('    disagreements: ' + ', '.join(row['disagreement_rules']))
            lines.extend('  Warning: ' + warning for warning in data['warnings'])
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    value = capture()
    atomic_json(STATE / 'capacity' / 'latest.json', value)
    print(json.dumps(value, indent=2, allow_nan=False) if args.json else format_report(value))


if __name__ == '__main__':
    main()

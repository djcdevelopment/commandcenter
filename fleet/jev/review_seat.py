"""AM4-only, fixed Dense review seat. No arbitrary launch configuration in requests.

`capture` is a one-time operator cutover action. All later start/stop calls are
bound to an owner and /proc start ticks, not a historical hardcoded PID.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import urllib.request
import fcntl

ROOT = Path('/home/derek/.config/fleet-review-seat')
PORT = 18090


def write(name, value):
    path = ROOT / name
    temp = path.with_suffix('.tmp')
    with temp.open('w') as out:
        os.chmod(temp, 0o600)
        json.dump(value, out)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)


def native(path):
    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}' + path, timeout=3) as response:
        return json.load(response)


def identity(pid):
    proc = Path('/proc') / str(pid)
    fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    if fields[0] == 'Z':
        raise ProcessLookupError('process_exited')
    argv = [s.decode() for s in (proc / 'cmdline').read_bytes().split(b'\0') if s]
    return {'pid': pid, 'start_ticks': fields[19],
            'argv_sha256': hashlib.sha256(json.dumps(argv).encode()).hexdigest()}


def stop(state):
    try:
        actual = identity(state['pid'])
    except (FileNotFoundError, ProcessLookupError):
        return
    if any(actual[k] != state[k] for k in actual):
        raise RuntimeError('process_identity_changed')
    try:
        slots = native('/slots')
    except Exception:
        if state.get('original'):
            raise RuntimeError('baseline_occupancy_unknown') from None
        slots = []  # This controller owns the failed/incomplete startup.
    if any(s.get('is_processing') is True or s.get('state', 0) not in (0, -1) for s in slots):
        raise RuntimeError('review_seat_busy')
    os.kill(state['pid'], signal.SIGTERM)
    for _ in range(100):
        try:
            identity(state['pid'])
        except (FileNotFoundError, ProcessLookupError):
            return
        time.sleep(.1)
    raise RuntimeError('owned_process_did_not_exit')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['capture', 'retire', 'start', 'stop', 'status', 'restore-baseline'])
    parser.add_argument('--owner', default='cutover')
    args = parser.parse_args()
    if not re.fullmatch(r'(cutover|jev-[0-9a-f]{24})', args.owner):
        raise SystemExit('invalid_owner')
    os.umask(0o077)
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (ROOT / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        config_file, state_file = ROOT / 'baseline.json', ROOT / 'state.json'
        state = json.loads(state_file.read_text()) if state_file.exists() else None
        if args.action == 'capture':
            if config_file.exists():
                raise RuntimeError('baseline_already_captured')
            listeners = subprocess.check_output(['ss', '-ltnp', f'sport = :{PORT}'], text=True)
            pids = set(re.findall(r'pid=(\d+)', listeners))
            if len(pids) != 1:
                raise RuntimeError('review_listener_not_unique')
            pid = int(pids.pop())
            proc = Path('/proc') / str(pid)
            argv = [s.decode() for s in (proc / 'cmdline').read_bytes().split(b'\0') if s]
            if not any('Qwen3.8-27B-Q4_K_M.gguf' in s for s in argv) or str(PORT) not in argv:
                raise RuntimeError('unexpected_baseline_model')
            environment = dict(s.decode().split('=', 1) for s in (proc / 'environ').read_bytes().split(b'\0') if b'=' in s)
            props = native('/props')
            if props.get('default_generation_settings', {}).get('n_ctx') != 131072 or props.get('total_slots') != 1:
                raise RuntimeError('unexpected_native_baseline_capacity')
            gpus = subprocess.check_output(['nvidia-smi', '--query-gpu=uuid', '--format=csv,noheader'], text=True).split()
            if len(gpus) != 2:
                raise RuntimeError('expected_two_am4_gpus')
            write('baseline.json', {'argv': argv, 'env': environment, 'cwd': str((proc / 'cwd').resolve()),
                                    'gpu_uuids': gpus, 'native_context': 131072})
            state = {**identity(pid), 'owner': 'cutover', 'original': True}
            write('state.json', state)
        elif args.action in ('retire', 'stop'):
            if state:
                if state['owner'] != args.owner:
                    raise RuntimeError('review_owner_mismatch')
                stop(state)
                write('state.json', None)
                state = None
        elif args.action in ('start', 'restore-baseline'):
            owner = 'cutover' if args.action == 'restore-baseline' else args.owner
            if state:
                if state['owner'] != owner:
                    raise RuntimeError('review_seat_owned_elsewhere')
                if identity(state['pid']) != {k: state[k] for k in ('pid', 'start_ticks', 'argv_sha256')}:
                    raise RuntimeError('process_identity_changed')
            else:
                config = json.loads(config_file.read_text())
                occupied = subprocess.check_output(['nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader,nounits'], text=True).strip()
                if occupied:
                    raise RuntimeError('am4_compute_owned_elsewhere')
                # The qualified baseline uses both cards. Preserve its exact argv/env;
                # UUID membership is checked, never infer placement from CUDA ordinal 0.
                actual = subprocess.check_output(['nvidia-smi', '--query-gpu=uuid', '--format=csv,noheader'], text=True).split()
                if set(actual) != set(config['gpu_uuids']):
                    raise RuntimeError('am4_gpu_inventory_changed')
                with (ROOT / 'model.log').open('ab') as log:
                    process = subprocess.Popen(config['argv'], env=config['env'], cwd=config['cwd'],
                                               stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
                state = {**identity(process.pid), 'owner': owner, 'original': False}
                write('state.json', state)
            end = time.monotonic() + 90
            while time.monotonic() < end:
                try:
                    if native('/health').get('status') == 'ok':
                        break
                except Exception:
                    time.sleep(1)
            else:
                raise RuntimeError('review_model_start_timeout')
        live = False
        if state:
            try:
                live = identity(state['pid']) == {k: state[k] for k in ('pid', 'start_ticks', 'argv_sha256')}
            except (FileNotFoundError, ProcessLookupError):
                pass
        print(json.dumps({'ok': True, 'owner': state.get('owner') if state else None,
                          'model_resident': live, 'native_context': 131072 if live else None,
                          'kv_enabled': False}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'ok': False, 'reason_code': str(error) if type(error) is RuntimeError else type(error).__name__}))
        raise SystemExit(1)

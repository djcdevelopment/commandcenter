#!/usr/bin/env python3
"""Protects drain-owned work in this experiment; it does not establish protection for every workload on the hardware.

Guarded seats: card2 (B70), card3 (B70), am4 (AM4 pair).
Latch file: $HEARTH_DISPATCH_PAUSE_FILE or ~/hearth-production/pause.dispatch

Usage:
    night_guard.py OUT.jsonl --card card2=omen-dense-27b --card card3=omen-dense-27b-b \
        [--am4 am4-vllm] [--interval 20] [--limit 104] [--am4-limit 90] \
        [--pause-file PATH] [--on-trip CMD] [--caller claude] [--simulate CARD]

Trips:
    - B70: VRAM >= --limit on two consecutive samples, or telemetry loss x2.
    - AM4: core >= --am4-limit on one sample, or telemetry loss x2.
    - --simulate CARD forces that card to read as tripped on the second sample.

Actions on trip (fixed order, all recorded):
    1. Write latch file + OUT.tripped + OUT.<card>.tripped (durable, fsync).
    2. Run --on-trip command once.
    3. Cancel non-terminal drain-owned jobs via door.

The guard never removes the latch file. While tripped, it re-records "trip-held" rows each interval.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import glob
import json
import math
import os
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

TERMINAL = {"succeeded", "failed", "cancelled", "expired", "rejected"}
CALLERS = {"codex": "codex-cli", "claude": "claude-frontier"}
DRAIN_PRINCIPAL = "bankedfire-drain"   # the jobs the guard may cancel: the drain's own, never the operator's or a reviewer's


def utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def durable(path, doc, append=False):
    """Write JSON document to path, fsync, atomic replace if not append."""
    path = Path(path)
    target = path if append else path.with_name(path.name + '.tmp')
    with target.open('a' if append else 'w') as fh:
        fh.write(json.dumps(doc) + '\n')
        fh.flush()
        os.fsync(fh.fileno())
    if not append:
        os.replace(target, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def guard_failure(out, exc):
    doc = {'utc': utc(), 'reason': 'guard failure', 'error': type(exc).__name__}
    durable(Path(str(out) + '.guard-failure'), doc)
    marker = Path(str(out) + '.tripped')
    if not marker.exists():
        durable(marker, doc)


def sample_card(card, sys_root=Path('/sys/class/drm')):
    """Sample B70 card temperatures from hwmon."""
    matches = glob.glob(str(sys_root / card / 'device/hwmon/hwmon*'))
    def value(name, scale=1):
        try:
            return int((Path(matches[0]) / name).read_text()) / scale
        except (IndexError, OSError, ValueError):
            return None
    return {
        "vram_c": value('temp3_input', 1000),
        "pkg_c": value('temp2_input', 1000),
        "fan_rpm": value('fan1_input'),
        "energy_uj": value('energy1_input')
    }


def sample_am4():
    """Sample AM4 GPU pair via SSH nvidia-smi. Returns list of dicts with core_c or None on failure."""
    try:
        result = subprocess.run(
            ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '10.44.0.2',
             'nvidia-smi --query-gpu=pci.bus_id,temperature.gpu --format=csv,noheader,nounits'],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=8, check=True
        )
        rows = []
        for line in result.stdout.splitlines():
            parts = [f.strip() for f in line.split(',')]
            if len(parts) >= 2:
                try:
                    rows.append({'pci': parts[0].lower(), 'core_c': float(parts[1])})
                except ValueError:
                    continue
        if len(rows) != 2:
            raise ValueError('expected 2 AM4 GPUs')
        return {'cores': rows, 'loss': False}
    except Exception:
        return {'cores': None, 'loss': True}


def pending_jobs(db, backend, principal_id):
    """Read projection read-only; find non-terminal jobs for given backend and principal id."""
    with closing(sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=2)) as conn:
        rows = conn.execute(
            "SELECT state_json FROM jobs WHERE status NOT IN "
            "('succeeded','failed','cancelled','expired','rejected')"
        )
        jobs = []
        for (raw,) in rows:
            job = json.loads(raw)
            args = (job.get('desired') or {}).get('arguments') or {}
            owner = job.get('principal') or {}
            if ((args.get('backend') == backend or job.get('provider') == backend)
                    and owner.get('id') == principal_id):
                jobs.append(job['job_id'])
        return jobs


def client_helpers():
    helper_path = str(Path.home() / 'work/delivery-plan/evidence')
    if helper_path not in sys.path:
        sys.path.insert(0, helper_path)
    from run_delivery_brief import _key, _body
    from hearth.callers.client import HearthClient
    return _key, _body, HearthClient


async def readiness(caller):
    key, _, client_class = client_helpers()
    async with client_class(key=key(caller)) as client:
        result = await client.call('list_operations')
        if not result.get('ok'):
            raise RuntimeError('authenticated door readiness refused')


async def cancel_jobs(jobs, caller, reason):
    key, body_of, client_class = client_helpers()
    out = []
    async with client_class(key=key(caller)) as client:
        for job in jobs:
            res = await client.call('cancel_execution', job_id=job, reason=reason)
            body = body_of(res)
            status = body.get('status') if isinstance(body, dict) else None
            out.append({'job_id': job, 'ok': res.get('ok'), 'status': status,
                        'unresolved': status not in TERMINAL})
    return out


def door_action(action, caller, jobs=None, reason=''):
    """Run door operation in a subprocess to avoid blocking the guard loop."""
    request = {'action': action, 'caller': caller, 'jobs': jobs or [], 'reason': reason}
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), '--door-action'],
        input=json.dumps(request), text=True, capture_output=True, timeout=6
    )
    if result.returncode:
        raise RuntimeError('guard door helper failed')
    return json.loads(result.stdout)


def door_child():
    request = json.load(sys.stdin)
    if request['action'] == 'readiness':
        asyncio.run(asyncio.wait_for(readiness(request['caller']), timeout=5))
        result = {'ready': True}
    else:
        result = asyncio.run(asyncio.wait_for(cancel_jobs(request['jobs'], request['caller'], request['reason']), timeout=5))
    print(json.dumps(result))


def run_on_trip(cmd):
    """Run --on-trip command. Returns dict with outcome."""
    try:
        proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            return {'ok': True, 'returncode': proc.wait(timeout=5)}
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            return {'ok': False, 'error': 'TimeoutExpired'}
    except Exception as exc:
        return {'ok': False, 'error': type(exc).__name__}


def write_latch(pause_file, card, reason, out_path):
    """Step 1: write latch and tripped markers. Must succeed before other steps."""
    doc = {"paused_by": "night_guard", "card": card, "utc": utc(), "reason": reason}
    pause_file.parent.mkdir(parents=True, exist_ok=True)
    durable(pause_file, doc)
    durable(Path(str(out_path) + '.tripped'), doc)
    durable(Path(f'{out_path}.{card}.tripped'), doc)


class GuardState:
    """Tracks per-card consecutive hot/loss counts for trip logic."""
    def __init__(self, cards, am4_active):
        self.cards = cards  # list of card names
        self.am4_active = am4_active
        self.hot_counts = {card: 0 for card in cards}
        self.loss_counts = {card: 0 for card in cards}
        if am4_active:
            self.am4_hot_count = 0
            self.am4_loss_count = 0

    def update_b70(self, card, data, limit, simulate_card, sample_number):
        """Returns (tripped: bool, reason: str or None)."""
        vram = data.get('vram_c')
        if card == simulate_card and sample_number >= 2:
            self.hot_counts[card] = 2
            return True, f'{card}: simulated trip'
        if vram is None:
            self.loss_counts[card] += 1
            self.hot_counts[card] = 0
            if self.loss_counts[card] >= 2:
                return True, f'{card}: telemetry unavailable on two consecutive samples'
            return False, None
        else:
            self.loss_counts[card] = 0
            if vram >= limit:
                self.hot_counts[card] += 1
            else:
                self.hot_counts[card] = 0
            if self.hot_counts[card] >= 2:
                return True, f'{card}: VRAM {vram} C >= {limit} C on two consecutive samples'
        return False, None

    def update_am4(self, data, limit, simulate_card, sample_number):
        """AM4 trips on one sample >= limit, or loss x2. Returns (tripped, reason)."""
        if simulate_card == 'am4' and sample_number >= 2:
            self.am4_hot_count = 1
            return True, 'am4: simulated trip'
        if data.get('loss'):
            self.am4_loss_count += 1
            self.am4_hot_count = 0
            if self.am4_loss_count >= 2:
                return True, 'am4: telemetry unavailable on two consecutive samples'
            return False, None
        self.am4_loss_count = 0
        cores = data.get('cores')
        if cores is None:
            self.am4_loss_count += 1
            if self.am4_loss_count >= 2:
                return True, 'am4: telemetry unavailable'
            return False, None
        max_c = max(c.get('core_c', 0) for c in cores) if cores else None
        if max_c is not None and max_c >= limit:
            self.am4_hot_count += 1
            return True, f'am4: core {max_c} C >= {limit} C'
        else:
            self.am4_hot_count = 0
        return False, None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('out', type=Path)
    ap.add_argument('--card', action='append', required=True, help='card2=omen-dense-27b')
    ap.add_argument('--am4', default=None, help='e.g. am4-vllm to enable AM4 guarding')
    ap.add_argument('--interval', type=float, default=20)
    ap.add_argument('--limit', type=float, default=104)
    ap.add_argument('--am4-limit', type=float, default=90)
    ap.add_argument('--pause-file', type=Path, default=None)
    ap.add_argument('--on-trip', default=None, help='Command to run once on trip')
    ap.add_argument('--caller', choices=('codex', 'claude'), default='claude')
    ap.add_argument('--simulate', default=None, help='CARD to force trip on second sample')
    ap.add_argument('--projection', type=Path, default=Path.home() / 'hearth-production/var/execution/projection.sqlite')
    ap.add_argument('--lock', type=Path, default=Path.home() / '.cache/night-guard.lock')
    a = ap.parse_args()

    if not math.isfinite(a.interval) or not 0 < a.interval <= 60:
        ap.error('interval must be in (0,60]')
    if not math.isfinite(a.limit) or not 0 < a.limit <= 150:
        ap.error('limit must be in (0,150]')
    if not math.isfinite(a.am4_limit) or not 0 < a.am4_limit <= 150:
        ap.error('am4-limit must be in (0,150]')

    # Parse card mappings
    card_mappings = {}
    for pair in a.card:
        card, sep, backend = pair.partition('=')
        if not sep or card not in ('card2', 'card3') or not backend or card in card_mappings:
            ap.error('unique --card card2|card3=backend entries required')
        card_mappings[card] = backend

    am4_backend = a.am4
    guarded_cards = list(card_mappings.keys())
    if am4_backend:
        guarded_cards.append('am4')

    if a.simulate and a.simulate not in guarded_cards:
        ap.error(f'--simulate must be one of: {guarded_cards}')

    # Resolve pause file
    if a.pause_file:
        pause_file = a.pause_file
    else:
        pause_file = Path(os.environ.get('HEARTH_DISPATCH_PAUSE_FILE', str(Path.home() / 'hearth-production/pause.dispatch')))

    principal_id = DRAIN_PRINCIPAL   # --caller only signs the cancel; the jobs are the drain's (candidate fix, review 2026-10-16)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.lock.parent.mkdir(parents=True, exist_ok=True)
    lock = a.lock.open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('a night guard already owns the lock')

    def stop_signal(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)

    # Pre-trip: check the door and the projection. Neither failure stops the guard (review 2026-10-16): the latch
    # must hold whatever else fails, so an unreachable door only means cancellation is recorded as unavailable.
    try:
        door_action('readiness', a.caller)
        door_available = True
    except Exception as exc:
        door_available = False
        durable(a.out, {'utc': utc(), 'event': 'door-unavailable', 'error': type(exc).__name__,
                        'note': 'latch and on-trip still run; cancellation will be recorded as unavailable'}, append=True)
    try:
        if card_mappings:
            pending_jobs(a.projection, next(iter(card_mappings.values())), principal_id)
        if am4_backend:
            pending_jobs(a.projection, am4_backend, principal_id)
    except Exception as exc:
        durable(a.out, {'utc': utc(), 'event': 'projection-unreadable', 'error': type(exc).__name__}, append=True)

    state = GuardState(guarded_cards, am4_backend is not None)
    sample_number = 0
    tripped_cards = set()
    on_trip_run_for = set()

    try:
        while True:
            started = time.monotonic()
            sample_number += 1

            # Collect samples
            row = {'utc': utc(), 'pid': os.getpid(), 'simulate': a.simulate}
            new_trips = []
            actions = {'utc': utc(), 'kind': 'actions'}

            # B70 cards
            for card, backend in card_mappings.items():
                data = sample_card(card)
                row[card] = data
                if card in tripped_cards:
                    # Already tripped: re-record trip-held
                    if a.simulate and card == a.simulate and sample_number >= 2:
                        continue
                    row['trip-held-' + card] = True
                    continue
                tripped, reason = state.update_b70(card, data, a.limit, a.simulate, sample_number)
                if tripped:
                    tripped_cards.add(card)
                    new_trips.append({'card': card, 'backend': backend, 'reason': reason})

            # AM4
            if am4_backend:
                am4_data = sample_am4()
                row['am4'] = {'core_c': am4_data.get('cores'), 'source': 'ssh', 'loss': am4_data.get('loss', False)}
                if 'am4' in tripped_cards:
                    row['trip-held-am4'] = True
                else:
                    tripped, reason = state.update_am4(am4_data, a.am4_limit, a.simulate, sample_number)
                    if tripped:
                        tripped_cards.add('am4')
                        new_trips.append({'card': 'am4', 'backend': am4_backend, 'reason': reason})

            # Handle new trips
            for trip in new_trips:
                card = trip['card']
                backend = trip['backend']
                reason = trip['reason']

                # Step 1: write latch (must succeed first)
                write_latch(pause_file, card, reason, a.out)

                steps = []
                # Step 1 recorded
                steps.append({'step': 1, 'action': 'write_latch', 'ok': True})

                # Step 2: run --on-trip once per card
                if card not in on_trip_run_for:
                    if a.on_trip:
                        result = run_on_trip(a.on_trip)
                        steps.append({'step': 2, 'action': 'on_trip', **result})
                        on_trip_run_for.add(card)
                    else:
                        steps.append({'step': 2, 'action': 'on_trip', 'ok': True, 'note': 'no --on-trip given'})

                # Step 3: cancel jobs
                try:
                    jobs = pending_jobs(a.projection, backend, principal_id)
                    if jobs:
                        cancelled = door_action('cancel', a.caller, jobs, f'night guard: {card} tripped')
                        steps.append({'step': 3, 'action': 'cancel', 'ok': True, 'cancelled': cancelled})
                    else:
                        steps.append({'step': 3, 'action': 'cancel', 'ok': True, 'note': 'no matching jobs'})
                except Exception as exc:
                    steps.append({'step': 3, 'action': 'cancel', 'ok': False, 'error': type(exc).__name__,
                                  'door_available': door_available})

                trip_row = {'utc': utc(), 'event': 'trip', 'card': card, 'reason': reason, 'steps': steps}
                durable(a.out, trip_row, append=True)
                actions.setdefault('trips', []).append(trip_row)

            # Record trip-held for cards already tripped (re-record each interval)
            for card in tripped_cards:
                if card not in [t['card'] for t in new_trips]:
                    # Check for new jobs to cancel if not yet cancelled or new ones appeared
                    if a.on_trip and card in on_trip_run_for:
                        pass  # Already ran on_trip once; per spec no repeated unless new jobs
                    backend = card_mappings.get(card) or am4_backend
                    try:
                        jobs = pending_jobs(a.projection, backend, principal_id)
                        if jobs:
                            cancelled = door_action('cancel', a.caller, jobs, f'night guard: {card} trip-held')
                            actions.setdefault('cancelled-held', []).extend(cancelled)
                    except Exception as exc:
                        actions.setdefault('cancel-errors', []).append({'card': card, 'error': type(exc).__name__})

            # Write main row
            durable(a.out, row, append=True)

            # Write actions if any
            if len(actions) > 2:
                durable(Path(str(a.out) + '.actions.jsonl'), actions, append=True)

            elapsed = time.monotonic() - started
            time.sleep(max(0, a.interval - elapsed))

    except SystemExit:
        # Signal received: write stopped row and exit 0
        stop_row = {'utc': utc(), 'event': 'stopped', 'pid': os.getpid()}
        durable(a.out, stop_row, append=True)
        lock.close()
        sys.exit(0)
    except BaseException as exc:
        # A crashed sampler cannot protect subsequent dispatches. Latch first,
        # just as for a thermal trip, before trying the optional stop command.
        write_latch(pause_file, 'guard', f'guard failure: {type(exc).__name__}', a.out)
        if a.on_trip:
            run_on_trip(a.on_trip)
        guard_failure(a.out, exc)
        raise
    finally:
        lock.close()


if __name__ == '__main__':
    if sys.argv[1:] == ['--door-action']:
        door_child()
    else:
        main()

#!/usr/bin/env python3
"""Campaign thermal guard. One process, independent card trips, owned jobs only.

OUT.tripped blocks campaign submissions; OUT.cardN.tripped identifies the card.
Telemetry loss trips immediately. A thermal trip requires two consecutive samples.
The execution projection is read-only: cancellation always goes through HEARTH.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import glob
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import math
import signal
from contextlib import closing

TERMINAL = {"succeeded", "failed", "cancelled", "expired", "rejected"}
CALLERS = {"codex": "codex-cli", "claude": "claude-frontier"}


def durable(path, doc, append=False):
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


def utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sample_card(card, sys_root=Path('/sys/class/drm')):
    matches = glob.glob(str(sys_root / card / 'device/hwmon/hwmon*'))
    def value(name, scale=1):
        try:
            return int((Path(matches[0]) / name).read_text()) / scale
        except (IndexError, OSError, ValueError):
            return None
    return {"vram_c": value('temp3_input', 1000), "pkg_c": value('temp2_input', 1000),
            "fan_rpm": value('fan1_input'), "energy_uj": value('energy1_input')}


def pending_jobs(db, backend, principal):
    with closing(sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=2)) as conn:
        rows = conn.execute("SELECT state_json FROM jobs WHERE status NOT IN "
                            "('succeeded','failed','cancelled','expired','rejected')")
        jobs = []
        for (raw,) in rows:
            job = json.loads(raw)
            args = (job.get('desired') or {}).get('arguments') or {}
            owner = job.get('principal') or {}
            if ((args.get('backend') == backend or job.get('provider') == backend)
                    and owner.get('type') == 'hearth_caller' and owner.get('id') == principal
                    and owner.get('authenticated') is True):
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


def stop_campaign(command, trip):
    env = dict(os.environ, BENCH27_TRIP_CARD=trip['card'], BENCH27_TRIP_BACKEND=trip['backend'])
    try:
        proc = subprocess.Popen(command, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, start_new_session=True)
        try:
            return {'returncode': proc.wait(timeout=5)}
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            return {'error': 'TimeoutExpired'}
    except Exception as exc:
        return {'error': type(exc).__name__}


def door_action(action, caller, jobs=None, reason=''):
    request = {'action': action, 'caller': caller, 'jobs': jobs or [], 'reason': reason}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--door-action'],
                            input=json.dumps(request), text=True, capture_output=True, timeout=6)
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


class Trips:
    def __init__(self, out, mappings, limit=104):
        self.out, self.mappings, self.limit = Path(out), mappings, limit
        self.hot = {card: 0 for card in mappings}

    def observe(self, samples):
        trips = []
        for card, backend in self.mappings.items():
            temp = samples[card]['vram_c']
            self.hot[card] = self.hot[card] + 1 if temp is not None and temp >= self.limit else 0
            if temp is not None and self.hot[card] < 2:
                continue
            marker = Path(f'{self.out}.{card}.tripped')
            if marker.exists():
                continue
            reason = (f'{card}: thermal telemetry unavailable' if temp is None else
                      f'{card}: VRAM {temp} C >= {self.limit} C on two consecutive samples')
            trip = {'utc': utc(), 'card': card, 'backend': backend, 'reason': reason}
            durable(marker, trip)
            # Global marker is deliberately sticky; clearing requires a reviewed recovery.
            global_marker = Path(str(self.out) + '.tripped')
            if not global_marker.exists():
                durable(global_marker, trip)
            trips.append(trip)
        return trips


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('out', type=Path)
    ap.add_argument('--card', action='append', required=True, help='card2=omen-dense-27b')
    ap.add_argument('--interval', type=float, default=20)
    ap.add_argument('--limit', type=float, default=104)
    ap.add_argument('--caller', choices=('codex', 'claude'), default='codex')
    ap.add_argument('--principal', help='optional assertion; must match authenticated caller mapping')
    ap.add_argument('--projection', type=Path, default=Path.home() / 'hearth-production/var/execution/projection.sqlite')
    ap.add_argument('--on-trip', help='Idempotent campaign stop command, invoked every interval while a card is tripped')
    ap.add_argument('--lock', type=Path, default=Path.home() / '.cache/bench27-card-guard.lock')
    a = ap.parse_args()
    if not math.isfinite(a.interval) or not 0 < a.interval <= 20 or not math.isfinite(a.limit) or not 0 < a.limit <= 104:
        ap.error('interval must be in (0,20] and thermal limit in (0,104]')
    if a.principal and a.principal != CALLERS[a.caller]:
        ap.error('principal must match caller')
    a.principal = CALLERS[a.caller]
    mappings = {}
    for pair in a.card:
        card, sep, backend = pair.partition('=')
        if not sep or card not in ('card2', 'card3') or not backend or card in mappings:
            ap.error('unique --card card2|card3=backend entries required')
        mappings[card] = backend
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.lock.parent.mkdir(parents=True, exist_ok=True)
    lock = a.lock.open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('a bench27 guard already owns the lock')
    trips = Trips(a.out, mappings, a.limit)
    try:
        door_action('readiness', a.caller)
        # Fail closed if projection cannot be read before declaring readiness.
        pending_jobs(a.projection, next(iter(mappings.values())), a.principal)
        while True:
            started = time.monotonic()
            row = {'utc': utc(), 'pid': os.getpid(), **{card: sample_card(card) for card in mappings}}
            row['new_trips'] = trips.observe(row)
            global_marker = Path(str(a.out) + '.tripped')
            for card in mappings:
                marker = Path(f'{a.out}.{card}.tripped')
                if marker.exists() and not global_marker.exists():
                    durable(global_marker, json.loads(marker.read_text()))
            # Persist heartbeat and trips BEFORE any command or network I/O.
            durable(a.out, row, append=True)
            actions = {'utc': utc(), 'kind': 'actions'}
            for card, backend in mappings.items():
                if not Path(f'{a.out}.{card}.tripped').exists():
                    continue
                trip = {'card': card, 'backend': backend}
                if a.on_trip:
                    actions.setdefault('stop', {})[card] = stop_campaign(a.on_trip, trip)
                try:
                    jobs = pending_jobs(a.projection, backend, a.principal)
                    if jobs:
                        cancelled = door_action('cancel', a.caller, jobs, f'card guard: {card} tripped')
                        actions.setdefault('cancelled', []).extend(cancelled)
                except Exception as exc:
                    actions.setdefault('cancel_errors', []).append({'card': card, 'error': type(exc).__name__})
            if len(actions) > 2:
                durable(Path(str(a.out) + '.actions.jsonl'), actions, append=True)
            time.sleep(max(0, a.interval - (time.monotonic() - started)))
    except BaseException as exc:
        guard_failure(a.out, exc)
        raise


if __name__ == '__main__':
    if sys.argv[1:] == ['--door-action']:
        door_child()
    else:
        main()

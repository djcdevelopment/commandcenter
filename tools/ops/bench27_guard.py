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

TERMINAL = {"succeeded", "failed", "cancelled", "expired", "rejected"}


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
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as conn:
        rows = conn.execute("SELECT state_json FROM jobs WHERE status NOT IN "
                            "('succeeded','failed','cancelled','expired','rejected')")
        jobs = []
        for (raw,) in rows:
            job = json.loads(raw)
            args = (job.get('desired') or {}).get('arguments') or {}
            if args.get('backend') == backend and (job.get('principal') or {}).get('id') == principal:
                jobs.append(job['job_id'])
        return jobs


async def cancel_jobs(jobs, caller, reason):
    # Reuse the existing credential loader; never expose its return value.
    sys.path.insert(0, str(Path.home() / 'work/delivery-plan/evidence'))
    from run_delivery_brief import _key, _body
    from hearth.callers.client import HearthClient
    out = []
    async with HearthClient(key=_key(caller)) as client:
        for job in jobs:
            res = await client.call('cancel_execution', job_id=job, reason=reason)
            body = _body(res)
            out.append({'job_id': job, 'ok': res.get('ok'), 'result': body})
    return out


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
            marker.write_text(json.dumps(trip) + '\n')
            # Global marker is deliberately sticky; clearing requires a reviewed recovery.
            global_marker = Path(str(self.out) + '.tripped')
            if not global_marker.exists():
                global_marker.write_text(json.dumps(trip) + '\n')
            trips.append(trip)
        return trips


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('out', type=Path)
    ap.add_argument('--card', action='append', required=True, help='card2=omen-dense-27b')
    ap.add_argument('--interval', type=float, default=20)
    ap.add_argument('--limit', type=float, default=104)
    ap.add_argument('--caller', choices=('codex', 'claude'), default='codex')
    ap.add_argument('--principal', default='codex-cli')
    ap.add_argument('--projection', type=Path, default=Path.home() / 'hearth-production/var/execution/projection.sqlite')
    ap.add_argument('--on-trip', help='Campaign stop command, invoked once per new trip')
    ap.add_argument('--lock', type=Path, default=Path.home() / '.cache/bench27-card-guard.lock')
    a = ap.parse_args()
    if a.interval <= 0 or a.limit > 104:
        ap.error('interval must be positive and thermal limit must not exceed 104 C')
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
    while True:
        row = {'utc': utc(), 'pid': os.getpid(), **{card: sample_card(card) for card in mappings}}
        new = trips.observe(row)
        for trip in new:
            if a.on_trip:
                try:
                    result = subprocess.run(a.on_trip, shell=True, capture_output=True, timeout=30)
                    trip['stop_returncode'] = result.returncode
                except Exception as exc:
                    trip['stop_error'] = type(exc).__name__
        # Retry cancellations after a trip, including jobs submitted in a race with the marker.
        for card, backend in mappings.items():
            if not Path(f'{a.out}.{card}.tripped').exists():
                continue
            try:
                jobs = pending_jobs(a.projection, backend, a.principal)
                row.setdefault('cancelled', []).extend(asyncio.run(cancel_jobs(jobs, a.caller, f'card guard: {card} tripped')) if jobs else [])
            except Exception as exc:
                row.setdefault('cancel_errors', []).append({'card': card, 'error': type(exc).__name__})
        row['new_trips'] = new
        with a.out.open('a') as fh:
            fh.write(json.dumps(row) + '\n')
        time.sleep(a.interval)


if __name__ == '__main__':
    main()

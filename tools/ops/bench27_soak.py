#!/usr/bin/env python3
"""Bounded offline-reviewed scheduler for a guarded paired workload; never changes profiles."""
from __future__ import annotations
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')


def verify(unit):
    for path, expected in {unit['workload']: unit['workload_sha256'], **unit['driver_sha256']}.items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise ValueError('frozen input hash changed: '+path)
    commands = unit['commands']
    if len(commands) != 1 or '--out' not in commands[0] or '--arm' not in commands[0]:
        raise ValueError('one explicit paired command required')
    command=commands[0]
    if (command[1] not in unit['driver_sha256'] or '--workload' not in command
            or command[command.index('--workload')+1] != unit['workload']
            or '--conversations' not in command or command[command.index('--conversations')+1] != '1'):
        raise ValueError('command must use pinned workload/driver at concurrency one')
    if Path(commands[0][1]).name != 'paired_thinking_workload.py':
        raise ValueError('reviewed paired driver required')


def seconds(utc):
    value = datetime.fromisoformat(utc.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('timezone required')
    return value.timestamp()


def report_pair(directory, expected_arm):
    paired = read(directory/'paired.json')
    calls = paired.get('calls')
    if not isinstance(calls, dict) or set(calls) != {'0','1'} or any(type(v) is not int or v < 0 for v in calls.values()):
        raise ValueError('unknown calls; never replace with zero')
    intervals = {}
    for seat in ('0','1'):
        path=directory/f'seat-{seat}'/'run.json'
        if not path.is_file():
            return calls,None  # Preserve known physical calls even when run metadata is absent.
        run = read(path)
        if run.get('arm') != expected_arm or run.get('concurrency') != 1:
            raise ValueError('wrong arm/concurrency')
        start,end = seconds(run['started_utc']),seconds(run['finished_utc'])
        if end < start:
            raise ValueError('reversed run interval')
        intervals[seat] = [start,end]
    return calls,intervals


def coverage(pairs):
    per_seat = {}
    for seat in ('0','1'):
        spans = [p['intervals'][seat] for p in pairs if p.get('intervals')]
        gaps = [max(0,b[0]-a[1]) for a,b in zip(spans,spans[1:])]
        per_seat[seat] = {'run_intervals_unix_s':spans,'run_wall_seconds':sum(b-a for a,b in spans),
                          'between_run_idle_seconds':gaps,'idle_seconds_total':sum(gaps)}
    overlap = sum(max(0,min(p['intervals']['0'][1],p['intervals']['1'][1])-max(p['intervals']['0'][0],p['intervals']['1'][0])) for p in pairs if p.get('intervals'))
    complete=[p for p in pairs if p.get('intervals')]
    gaps=[max(0,min(b['intervals']['0'][0],b['intervals']['1'][0])-max(a['intervals']['0'][1],a['intervals']['1'][1])) for a,b in zip(complete,complete[1:])]
    measured=(max(complete[-1]['intervals'][s][1] for s in ('0','1'))-min(complete[0]['intervals'][s][0] for s in ('0','1'))) if complete else 0
    return {'measured_campaign_span_seconds':measured,'per_seat':per_seat,'both_run_overlap_seconds':overlap,'global_between_pair_gaps_seconds':gaps,
            'interpretation':'Run wall intervals include prefill, generation and client overhead, not proof of continuous GPU utilization. Counter/thermal records and gaps require review; no report acceptance inferred.'}


# Harness escalates after 30 s; this owned child is in its own session, so
# escalation MUST finish sooner even when the harness initiated the stop.
CANCEL_GRACE_SECONDS = 25


def signal_group(child, sig):
    try:
        os.killpg(child.pid, sig)
    except ProcessLookupError:
        pass  # Child/group exited between poll and signal.


def stop_child(child):
    if child is None or child.poll() is not None:
        return
    signal_group(child, signal.SIGTERM)
    try:
        child.wait(timeout=CANCEL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        signal_group(child, signal.SIGKILL)
        child.wait()


def run(args):
    if not 7200 <= args.minimum_seconds < args.maximum_seconds <= 8400:
        raise ValueError('minimum >=7200, maximum <=8400, with positive grace required')
    if hashlib.sha256(Path(args.unit).read_bytes()).hexdigest() != args.unit_sha256:
        raise ValueError('unit sequence hash changed')
    unit=read(args.unit);verify(unit)
    out=Path(args.out);out.mkdir(parents=True,exist_ok=False)
    child=None;stop=False
    def interrupt(signum,frame):
        nonlocal stop
        stop=True
        if child is not None and child.poll() is None:
            signal_group(child,signal.SIGTERM)
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,interrupt)
    start=time.monotonic();pairs=[];totals={'0':0,'1':0};known_totals={'0':0,'1':0};unknown_pairs=[];status='incomplete';error=None
    try:
        while not stop and time.monotonic()-start < args.maximum_seconds:
            verify(unit)
            directory=out/f'pair-{len(pairs)+1:04d}'
            command=list(unit['commands'][0]);command[command.index('--out')+1]=str(directory)
            arm=command[command.index('--arm')+1]
            with (out/f'{directory.name}.stdout').open('w') as log:
                child=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                while child.poll() is None and not stop and time.monotonic()-start < args.maximum_seconds:
                    time.sleep(.1)
                timed_out=child.poll() is None
                if timed_out:
                    stop_child(child)
                row={'directory':str(directory),'returncode':child.returncode,'interrupted_or_timeout':timed_out or stop}
            try:
                calls,intervals=report_pair(directory,arm);row.update(calls=calls,intervals=intervals)
                known_totals={s:known_totals[s]+calls[s] for s in known_totals}
                totals=dict(known_totals)
            except (OSError,ValueError,KeyError,TypeError) as exc:
                row.update(calls=None,error=str(exc));totals={'0':None,'1':None}
                unknown_pairs.append(str(directory))
            pairs.append(row)
            measured=coverage(pairs)
            write(out/'progress.json',{'pairs':pairs,'calls':totals,'known_calls':known_totals,'unknown_pairs':unknown_pairs,'coverage':measured})
            if stop or timed_out or child.returncode != 0 or row.get('calls') != {'0':2,'1':2} or not row.get('intervals'):
                status='model_failed' if child.returncode == 4 and not stop and not timed_out else 'failed';break
            if measured['measured_campaign_span_seconds'] >= args.minimum_seconds + 1:
                status='duration_met_pending_stability_review';break
    except Exception as exc:
        status='failed';error=type(exc).__name__+': '+str(exc)
    finally:
        stop_child(child)
        summary={'schema':'bench27-soak.v1','status':status,'error':error,'calls':totals,'known_calls':known_totals,'unknown_pairs':unknown_pairs,'pairs':pairs,'coverage':coverage(pairs),'timestamp_resolution_seconds':1,'required_recorded_span_seconds':args.minimum_seconds+1,'minimum_seconds':args.minimum_seconds,'maximum_seconds':args.maximum_seconds,'qualification':'None; duration/stability observations only'}
        write(out/'summary.json',summary)
        print(json.dumps(summary,allow_nan=False))
    return 0 if status=='duration_met_pending_stability_review' else 4 if status=='model_failed' else 3


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unit-sha256',required=True)
    parser.add_argument('--unit',required=True,type=Path);parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--minimum-seconds',type=int,default=7200);parser.add_argument('--maximum-seconds',type=int,default=8400)
    return run(parser.parse_args())

if __name__=='__main__':raise SystemExit(main())

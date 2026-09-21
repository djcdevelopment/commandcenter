"""Run pinned Hermes once, export its final answer, release the review seat."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import time

from fleet.jev.client import atomic_json
from fleet.jev.review_evidence import compact_review_packet


def run_hermes(packet, root, run_budget_s=180):
    if type(run_budget_s) is not int or not 30 <= run_budget_s <= 180:
        raise ValueError('review_budget_out_of_bounds')
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    original_packet = packet
    packet, packing = compact_review_packet(packet)
    original_path = root / 'packet-original.md'
    original_path.write_text(original_packet, encoding='utf-8')
    os.chmod(original_path, 0o600)
    packet_path = root / 'packet.md'
    packet_path.write_text(packet, encoding='utf-8')
    os.chmod(packet_path, 0o600)
    packet_evidence = {**packing,
        'original_bytes': len(original_packet.encode('utf-8')),
        'sent_bytes': len(packet.encode('utf-8')),
        'original_sha256': hashlib.sha256(original_packet.encode('utf-8')).hexdigest(),
        'sent_sha256': hashlib.sha256(packet.encode('utf-8')).hexdigest()}
    atomic_json(root / 'packet-evidence.json', packet_evidence)
    profile = Path('/home/derek/.config/hermes-fleet')
    environment = dict(os.environ)
    environment.update(HERMES_HOME=str(root), HERMES_AM4_KEY=(profile / 'am4.key').read_text().strip(),
                       HERMES_REVIEW_KEY=Path('/home/derek/.config/fleet-scheduler/reviewer.key').read_text().strip())
    provider = {'provider': 'custom', 'model': 'am4-dense-27b', 'base_url': 'http://192.168.12.233:8090/v1',
                'api_key': environment['HERMES_AM4_KEY'], 'timeout': 120, 'extra_body': {'reasoning_effort': 'none'}}
    configuration = {'model': {'provider': 'custom', 'default': 'am4-dense-27b', 'base_url': provider['base_url'],
        'api_key': provider['api_key'], 'context_length': 131072}, 'providers': {'custom': provider},
        'compression': {'enabled': True, 'threshold': .85, 'context_total_ceiling_seconds': 120},
        'auxiliary': {'compression': provider, 'title_generation': {'enabled': False}, 'background_review': {'enabled': False}},
        'mcp_servers': {'hearth': {'url': 'http://127.0.0.1:8713/mcp', 'headers': {'X-Hearth-Key': '${HERMES_REVIEW_KEY}'}}},
        'platform_toolsets': {'cli': ['hearth']}, 'display': {'interface': 'cli'}}
    atomic_json(root / 'config.yaml', configuration)  # JSON is valid YAML; private directory/file.
    environment['OPENAI_API_KEY'] = provider['api_key']
    environment['OPENAI_BASE_URL'] = provider['base_url']
    command = ['/home/derek/.local/share/hermes-fleet/venv/bin/hermes', 'chat', '--cli', '--oneshot',
               '--ignore-rules', '--reasoning', 'none', '--toolsets', 'hearth', '--max-turns', '4',
               '--run-budget', str(run_budget_s), '--query-file', str(packet_path)]
    start = time.monotonic()
    with (root / 'transcript.log').open('wb') as log:
        process = subprocess.Popen(command, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                   cwd=root, start_new_session=True)
        try:
            code = process.wait(timeout=run_budget_s + 20)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGINT)
            try:
                code = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise RuntimeError('hermes_review_deadline') from None
    if code != 0 or not (root / 'state.db').exists():
        raise RuntimeError('hermes_review_failed')
    with sqlite3.connect('file:' + str(root / 'state.db') + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM messages WHERE role='assistant' ORDER BY id DESC LIMIT 1").fetchone()
        if row is None or row['tool_calls'] not in (None, '', '[]') or not row['content']:
            raise RuntimeError('hermes_final_answer_missing')
        report = row['content']
    metadata = {'model': 'am4-dense-27b', 'reviewer': 'hermes', 'elapsed_s': round(time.monotonic() - start, 3),
                'run_budget_s': run_budget_s,
                'source': 'unedited Hermes final assistant message', 'message_id': row['id'],
                'kv_reused': False, 'packet_evidence': packet_evidence}
    atomic_json(root / 'result.json', {'report': report, 'metadata': metadata})
    return report, metadata


async def run_review(task_id, call, home):
    response = await call('scheduler_review', {'task_id': task_id})
    try:
        report, metadata = await asyncio.to_thread(run_hermes, response['packet'], home / 'reviews' / task_id)
    except Exception:
        await call('scheduler_review', {'task_id': task_id, 'metadata': {'failed': True}})
        raise
    await call('scheduler_review', {'task_id': task_id, 'report': report, 'metadata': metadata})

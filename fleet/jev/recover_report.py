"""Delivery only: submit an already saved, unedited Hermes result; no inference."""
import asyncio
import json
from fleet.jev.cli import HOME, connect_call

TASK = 'jev-8bc92b76ef510e1e3e75c555'

if __name__ == '__main__':
    value = json.loads((HOME / 'reviews' / TASK / 'result.json').read_text())
    metadata = {**value['metadata'], 'delivery_recovery': 'Codex repaired proven empty-argv startup identity; no inference repeated'}
    answer = asyncio.run(connect_call('scheduler_review', {'task_id': TASK, 'report': value['report'], 'metadata': metadata}))
    print(json.dumps(answer))

"""Read operator-curated outcomes; expose fixed numeric feedback, never free prose."""
import copy
import hashlib
import json
from pathlib import Path
import re

from fleet.jev.quality_counts import summarize_quality

PROFILE = 'omen-resident-hearth'


def load_quality(path):
    try:
        path = Path(path)
        if path.stat().st_size > 131072:
            raise ValueError('oversized_quality_history')
        raw = path.read_bytes()
        document = json.loads(raw.decode('utf-8-sig'))
        if (not isinstance(document, dict) or document.get('schema') != 'fleet-work-quality.v1'
                or document.get('verified_by') != 'operator' or document.get('profile') != PROFILE):
            raise ValueError('unrecognized_quality_history')
        records = document.get('records')
        if not isinstance(records, list) or len(records) > 1000:
            raise ValueError('invalid_quality_records')
        identities = []
        for record in records:
            if not isinstance(record, dict):
                raise ValueError('invalid_quality_record')
            identity = record.get('task_id')
            if not isinstance(identity, str) or not re.fullmatch(r'jev-[0-9a-f]{24}', identity):
                raise ValueError('invalid_quality_identity')
            identities.append(identity)
        if len(identities) != len(set(identities)):
            raise ValueError('duplicate_quality_task')
        counts = summarize_quality(records)
        if counts['invalid_records']:
            raise ValueError('unclassified_quality_record')
        return {**counts, 'profile': PROFILE, 'history_sha256': hashlib.sha256(raw).hexdigest(),
                'scope': 'Operator-verified selected task subset; not inference success or a benchmark.'}
    except (OSError, UnicodeError, ValueError, TypeError):
        return {'available': False, 'reason': 'quality_history_unavailable'}


def quality_line(summary):
    if summary.get('available') is not True:
        return None
    return ('Operator-verified task subset: worker_tasks={authored}; unmodified_delivery='
            '{unmodified_delivery}; assisted_delivery={assisted_delivery}; rejected={rejected}; '
            'incomplete={incomplete}; not_run={not_run}. Not a benchmark or automatic acceptance.').format(**summary)


def with_quality(prepared, summary):
    """Copy metadata; no authority, route, task identity or capacity changes."""
    line = quality_line(summary)
    if line is None:
        return prepared
    result = copy.deepcopy(prepared)
    for row in result.get('candidates', []):
        if row.get('profile') == PROFILE:
            row['recent_outcomes'] = [line] + row.get('recent_outcomes', [])[:3]
    return result

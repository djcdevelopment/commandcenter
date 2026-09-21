"""Codex fallback: display declared registry facts, never infer runtime readiness."""


def summarize_registry(loops, harnesses):
    def dictionaries(value):
        return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []

    def text(value):
        return value if isinstance(value, str) else 'unknown'

    def row(kind, group, item):
        return {'kind': kind, 'group': text(group), 'id': text(item.get('id')),
                'declared_status': text(item.get('status')),
                'entrypoint': text(item.get('entrypoint')),
                'evidence': text(item.get('last_evidence'))}

    result = []
    loops = loops if isinstance(loops, dict) else {}
    harnesses = harnesses if isinstance(harnesses, dict) else {}
    for loop in dictionaries(loops.get('loop')):
        for item in dictionaries(loop.get('implementation')):
            result.append(row('implementation', loop.get('id'), item))
    for item in dictionaries(harnesses.get('harness')):
        result.append(row('harness', 'harness', item))
    return sorted(result, key=lambda item: (item['kind'], item['group'], item['id']))

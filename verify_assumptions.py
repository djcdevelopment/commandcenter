import json
from pathlib import Path

root = Path('.')
files = [
    'findings.json',
    'capabilities.json',
    'associations.json',
    'coverage.json',
    'policy.json',
    'capacity_estimates.json',
    'prediction_accuracy.json',
    'experiment_candidates.json',
    'experiment_results.json',
    'known_good_models.json',
    'known_bad_models.json',
    'policy_overrides.json'
]

for f in files:
    p = root / 'knowledge' / f
    if p.exists():
        try:
            data = json.loads(p.read_text())
            watermark = data.get('evidence_watermark')
            count = (data.get('observation_count') or 
                    data.get('capability_count') or 
                    data.get('association_count') or 
                    data.get('source_findings') or 
                    data.get('plan_count') or 
                    (len(data.get('entries', [])) if 'entries' in data else None))
            print(f'{f}: watermark={watermark}, count={count}')
        except Exception as e:
            print(f'{f}: error={e}')
    else:
        print(f'{f}: missing')

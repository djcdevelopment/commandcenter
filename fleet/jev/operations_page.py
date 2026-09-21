"""Capture one human/agent fleet operating map using existing registries and probes."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib

from fleet.jev.capacity import capture
from fleet.jev.capacity_html import render_capacity_page, table, text
from fleet.jev.local import ROOT, STATE
from fleet.jev.registry_rows import summarize_registry
from fleet.jev.quality import load_quality


def collect():
    registries, sources = {}, {}
    for name in ('loops', 'harnesses'):
        path = ROOT / 'hearth/etc' / (name + '.toml')
        raw = path.read_bytes()
        registries[name] = tomllib.loads(raw.decode('utf-8-sig'))
        sources[name] = {'path': path.relative_to(ROOT).as_posix(),
                         'sha256': hashlib.sha256(raw).hexdigest()}
    return {'schema': 'fleet-operations.v1', 'advisory_only': True,
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'source_files': sources, 'registries': registries,
            'registry_rows': summarize_registry(registries['loops'], registries['harnesses']),
            'pilot_configuration': {
                'route': 'mechnet_build', 'builder': 'cc-builder-2',
                'profile': 'omen-resident-hearth', 'scheduler_host': 'FX99 CPU',
                'reviewer': 'Hermes on FX99; AM4 Dense 27B on demand',
                'review_native_context': 131072, 'review_slots': 1,
                'promotion': 'manual; Hermes findings are advisory, not acceptance',
                'kv_reuse_demonstrated': False,
                'scope': 'Qualified configuration, not fresh service health or permission to dispatch'},
            'quality': load_quality(ROOT / 'fleet/jev/quality-history.json'),
            'capacity': capture()}


def render(document):
    pilot = document['pilot_configuration']
    intro = '<section><h2>What controls what</h2>' + table(
        ['Layer', 'Access and control', 'Observation / feedback'], [
            ['Hearth gateway', 'Scoped inference, backend pins, task-family routing; planning and build-request tools',
             'Execution job IDs, routing metadata, readiness, receipts; inference success is not code acceptance'],
            ['DeepAgents harness', 'create_deep_agent; scoped filesystem tools; named evidence-worker / independent-verifier harness',
             'Artifacts and critic findings; the named run-flash-dense harness is not promoted by its existence'],
            ['MechNet execution', 'submit_task / task_status; bounded isolated builds, explicit deliverables, builder/runner selection',
             'Worker result, candidate commit, missing deliverables, acceptance evidence; manual promotion']])
    intro += '<p>These are composable layers, not three mutually exclusive GPU pools.</p></section>'
    intro += '<section><h2>Qualified pilot configuration — not live status</h2>'
    intro += table(['Field', 'Declared value'], [[key, value] for key, value in pilot.items()])
    intro += ('<p>JEV ranks approved task metadata for this fixed route. It does not yet choose '
              'among all routes, rotate models, allocate GPUs, or invoke MemSplice. The scheduler '
              'holds credentials; JEV receives only allowlisted summaries and numeric outcomes.</p>'
              '<p>Hermes has returned PASS while listing real defects. Treat findings as advisory. '
              'Accepted correct artifacts, receipt completion and inference success are different signals. '
              'Logs and knowledge projections are not automatic weight training.</p></section>')
    intro += '<section><h2>Operator-verified work quality</h2>'
    quality = document.get('quality')
    if isinstance(quality, dict) and quality.get('available') is True:
        metrics = ('tasks', 'authored', 'unmodified_delivery', 'assisted_delivery',
                   'rejected', 'incomplete', 'not_run')
        intro += table(['Metric', 'Count'], [
            ['Reached worker (not successful output)' if name == 'authored' else name,
             quality.get(name)] for name in metrics])
        intro += '<p>History SHA256: ' + text(quality.get('history_sha256')) + '</p>'
        intro += '<p>Selected operator-verified task subset; not a benchmark or automatic acceptance.</p>'
    else:
        intro += '<p>Work quality unavailable; no verified history was loaded.</p>'
    intro += '</section>'
    intro += ('<section><h2>Declared registry — historical evidence included</h2>'
              '<p>LIVE below is a registry declaration, not a fresh readiness probe or pilot admission. '
              'The old AM4 4k/18084 and reader/18085 entries do not describe the current '
              'on-demand 128k reviewer on native 18090. Inspect dated evidence before use.</p>')
    keys = ('kind', 'group', 'id', 'declared_status', 'entrypoint', 'evidence')
    intro += table(['Kind', 'Loop/group', 'ID', 'Declared status', 'Entry point', 'Dated evidence'],
                   [[row[key] for key in keys] for row in document['registry_rows']])
    intro += '</section><section><h2>Planning and observation limits</h2><p>'
    intro += ('Planning validity is 300 seconds; per-field freshness is separate: occupancy 30s, '
              'readiness 120s, reachability 300s, trial runway 3600s. The capacity observations '
              'below use their own acquisition times and TTLs. Unknown B70 free VRAM stays unknown. '
              'Capacity observations are not yet connected to a multi-route allocation policy.</p>'
              '<p>Source commit: ' + text(document['source_commit']) + '. Exact registry hashes '
              'and parsed source records are embedded for agents. Configuration summaries were '
              'source-checked on 2026-09-21; recapture hardware before placement.</p></section>')
    page = render_capacity_page(document['capacity'])
    page = page.replace('<title>Fleet capacity snapshot</title>', '<title>Fleet operations snapshot</title>')
    page = page.replace('<h1>Fleet capacity snapshot</h1>', '<h1>Fleet operations snapshot</h1>')
    page = page.replace('</style>', '.scroll td,.scroll th{overflow-wrap:anywhere}</style>')
    page = page.replace('<section>', intro + '<section>', 1)
    payload = json.dumps(document, ensure_ascii=True, allow_nan=False).replace('<', '\\u003c')
    return page.replace('</body>', '<script type="application/json" id="fleet-operations">'
                        + payload + '</script></body>')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, help='New HTML path; never overwrites an existing file.')
    args = parser.parse_args()
    target = args.out or STATE / 'capacity' / (
        'operations-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.html')
    if target.exists():
        raise FileExistsError('operations_page_output_exists')
    document = collect()
    page = render(document)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf-8') as output:
        output.write(page)
    print(json.dumps({'path': str(target.resolve()), 'captured_at': document['capacity']['captured_at'],
                      'bytes': len(page.encode('utf-8')), 'advisory_only': True}))


if __name__ == '__main__':
    main()

"""Codex fallback renderer after the bounded Hermes authoring run timed out."""
from html import escape
import json


def text(value):
    return escape('unknown' if value is None else str(value), quote=True)


def table(headers, rows):
    return ('<div class="scroll"><table><thead><tr>'
            + ''.join('<th>' + text(value) + '</th>' for value in headers)
            + '</tr></thead><tbody>'
            + ''.join('<tr>' + ''.join('<td>' + text(value) + '</td>' for value in row)
                      + '</tr>' for row in rows) + '</tbody></table></div>')


def render_capacity_page(document):
    sections = []
    for name, observation in document.get('observations', {}).items():
        available = observation.get('available') is True
        content = '<h2>' + text(name) + '</h2><p class="badge">' + (
            'Observed' if available else 'Unknown') + '</p>'
        content += '<p class="time">Acquisition: ' + text(observation.get('observed_from'))
        content += ' to ' + text(observation.get('observed_until'))
        content += '; TTL ' + text(observation.get('ttl_s')) + ' seconds.</p>'
        data = observation.get('data') or {}
        if not available:
            content += '<p>This source was unavailable. No capacity is inferred.</p>'
        elif name == 'omen_worker':
            content += table(['Model', 'Ready', 'Free slots', 'Total slots', 'Tokens per slot'],
                             [[data.get(key) for key in ('model', 'ready', 'free_slots',
                                                       'parallel_slots', 'context_length')]])
            content += '<p>Slots describe request capacity, not free VRAM.</p>'
        elif name == 'omen_b70':
            content += table(['BDF', 'Adapter', 'Adapter local committed (bytes)',
                              'Adapter non-local committed (bytes)',
                              'Observer process only (bytes)', 'Free memory'],
                             [[row.get('bdf'), row.get('name'),
                               row.get('adapter_local_committed_bytes'),
                               row.get('adapter_non_local_committed_bytes'),
                               row.get('observer_process_local_bytes'), 'unknown']
                              for row in data.get('adapters', [])])
            for row in data.get('adapters', []):
                content += '<p>' + text(row.get('bdf')) + ' disagreements: ' + text(
                    ', '.join(row.get('disagreement_rules', [])) or 'none reported') + '</p>'
            content += '<ul>' + ''.join('<li>' + text(warning) + '</li>'
                                       for warning in data.get('warnings', [])) + '</ul>'
            content += '<details><summary>Field sources</summary><dl>' + ''.join(
                '<dt>' + text(key) + '</dt><dd>' + text(value) + '</dd>'
                for key, value in data.get('field_sources', {}).items()) + '</dl></details>'
        elif name == 'am4_nvidia':
            content += table(['Adapter', 'UUID', 'Total (MiB)', 'Used (MiB)',
                              'Driver-reported free (MiB)'],
                             [[row.get(key) for key in ('name', 'uuid', 'total_mib',
                                                       'used_mib', 'free_mib')]
                              for row in data.get('adapters', [])])
            content += '<p>Driver-reported free memory is not proof that a model fits.</p>'
        sections.append('<section>' + content + '</section>')
    embedded = json.dumps(document, ensure_ascii=True, allow_nan=False).replace('<', '\\u003c')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Fleet capacity snapshot</title><style>'
            'body{font:16px/1.5 system-ui,sans-serif;background:#101820;color:#e7edf3;'
            'margin:0}main{max-width:1280px;margin:auto;padding:32px 20px}'
            'h1{font-size:2rem;margin-bottom:8px}h2{font-size:1.25rem}'
            '.notice{border-left:4px solid #ffc36b;padding:12px 18px;background:#28271f}'
            'section{background:#1c2935;padding:20px;margin:20px 0;border-radius:12px}'
            '.time,footer{color:#b7c7d7;font-size:.9rem}.badge{display:inline-block;'
            'border:1px solid #8da7be;border-radius:20px;padding:2px 12px}'
            '.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%}'
            'th,td{text-align:left;border-bottom:1px solid #425365;padding:10px;'
            'vertical-align:top}th{color:#91d2df}dt{font-weight:600}dd{margin-bottom:8px}'
            '</style></head><body><main><h1>Fleet capacity snapshot</h1>'
            '<p class="notice">Snapshot, not live. Advisory only: not a reservation '
            'or permission to load a model. Recapture before dispatch or placement.</p>'
            '<p>Captured: ' + text(document.get('captured_at')) + '</p>'
            + ''.join(sections)
            + '<footer>Agent data: the complete original observation document is embedded '
            'as application/json in script#fleet-capacity. No external assets or executable '
            'JavaScript. Observations become stale; this page does not refresh itself.</footer>'
            '</main><script type="application/json" id="fleet-capacity">'
            + embedded + '</script></body></html>')

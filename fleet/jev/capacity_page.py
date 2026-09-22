"""Capture the actual fleet once and save a human/agent-readable HTML snapshot."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from fleet.jev.capacity import capture
from fleet.jev.capacity_html import render_capacity_page
from fleet.jev.local import STATE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, help='New HTML file; existing files are never overwritten.')
    args = parser.parse_args()
    target = args.out or STATE / 'capacity' / (
        'capacity-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.html')
    if target.exists():
        raise FileExistsError('capacity_page_output_exists')
    document = capture()
    page = render_capacity_page(document)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf-8') as output:
        output.write(page)
    print(json.dumps({'path': str(target.resolve()), 'captured_at': document['captured_at'],
                      'advisory_only': True, 'bytes': len(page.encode('utf-8'))}))


if __name__ == '__main__':
    main()

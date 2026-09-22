"""Derek-approved single retry: change metadata, not the requested code/gates."""
import json
from fleet.jev.local import environment
from hearth.toolsurface.jev_scheduler import connection

SUMMARY = ('Approved single-file Python edit: extend an existing pure status-formatting function, not the scheduler or hardware itself. '
    'The worker receives the complete original 40-line function and exact acceptance criteria locally. '
    'Inputs are supplied dictionaries. Without a scheduler dictionary preserve the old four-line output byte for byte. '
    'With scheduler.ready exactly true show a CPU-only JEV scheduler as ready independently of controller readiness; '
    'reviewer.state unloaded means intentionally unloaded, not failed. Show active-work and pending-review counts, accepting only nonnegative integers (not booleans); otherwise unknown. '
    'Wait text comes only from a specified five-label allowlist; any other value is unknown. Preserve worker-capacity formatting and the final newline. '
    'Write the complete changed file and syntax-check it. No tests, dependencies, network calls, deployment or hardware control. '
    'The worker already has file-write and Python tools. No design or authorization choice remains open for this edit.')

if __name__ == '__main__':
    environment()
    assert len(SUMMARY) <= 1200
    with connection() as db:
        row = db.execute("SELECT * FROM jobs WHERE id='jev-8bc92b76ef510e1e3e75c555'").fetchone()
        if row is None or row['state'] != 'ready' or row['receipt_id']:
            raise SystemExit('original_undispatched_task_required')
        document = json.loads(row['document'])
        document['summary'] = SUMMARY
        document['build_seconds'] = 180  # Smaller ceiling keeps the original delivery reserve.
        db.execute('UPDATE jobs SET document=? WHERE id=?', (json.dumps(document), row['id']))
    print(json.dumps({'clarified_task': row['id'], 'summary_chars': len(SUMMARY), 'gates_unchanged': True}))

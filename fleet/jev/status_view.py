import math
import re

def render_status(status, daemon_status, now):
    status = status if isinstance(status, dict) else {}

    # Helper function to validate and return allowed values
    def label(name, allowed):
        value = status.get(name)
        return value if isinstance(value, str) and value in allowed else 'unknown'

    # Extract and validate daemon status
    if not isinstance(daemon_status, str) or daemon_status not in ('running', 'stopped', 'unknown'):
        daemon_status = 'unknown'

    # Extract and validate scheduler state
    scheduler = label('scheduler', {'ready', 'held', 'not_started'})

    # Extract and validate reviewer state
    reviewer = label('reviewer', {'unloaded', 'requested', 'recovery_required', 'unknown'})

    # Extract and validate pending reviews
    pending = status.get('pending_reviews')
    pending = str(pending) if type(pending) is int and pending >= 0 else 'unknown'

    # Extract and validate active build ID
    build = status.get('active_build')
    build = ('none' if build is None else build if isinstance(build, str)
             and re.fullmatch(r'br-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}', build) else 'unknown')

    # Extract and validate observed_at and compute age
    observed = status.get('observed_at')
    age = 'unknown'
    if (type(observed) in (int, float) and type(now) in (int, float)
            and 0 <= observed <= now and math.isfinite(now) and math.isfinite(observed)):
        seconds = now - observed
        age = f'{seconds:.1f} seconds (' + ('stale' if seconds > 90 else 'fresh') + ')'

    # Extract and validate wait_reason
    wait_reason = status.get('wait_reason')
    wait_label = wait_reason if isinstance(wait_reason, str) else 'unknown'
    valid_wait_reasons = {
        'idle', 'work_in_progress', 'worker_unavailable',
        'review_recovery_required', 'failed_task_requires_operator',
        'no_admissible_ready_task', 'needs_clarification',
        'unchanged_decision_inputs', 'review_evidence_pending'
    }
    if wait_reason is None:
        wait_label = 'none'
    elif wait_label not in valid_wait_reasons:
        wait_label = 'unknown'

    # Return the formatted string with the new wait reason line
    return '\n'.join((
        'Daemon state: ' + daemon_status,
        'Cached snapshot age: ' + age,
        'Last cycle scheduler state: ' + scheduler,
        'Last observed active build: ' + build,
        'Last observed pending review count: ' + pending,
        'LAST OBSERVED reviewer state: ' + reviewer,
        'Last observed wait reason: ' + wait_label,
        'Note: cached observations are not live hardware residency',
    ))

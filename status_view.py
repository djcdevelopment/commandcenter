import math
import re

def render_status(status, daemon_status, now):
    status = status if isinstance(status, dict) else {}
    
    # Helper function to validate and return allowed values
    def label(name, allowed):
        value = status.get(name)
        return value if isinstance(value, str) and value in allowed else 'unknown'
    
    # Extract and validate daemon status
    if daemon_status not in ('running', 'stopped', 'unknown'):
        daemon_status = 'unknown'
    
    # Extract and validate scheduler state
    scheduler = label('scheduler', {'ready', 'held', 'not_started'})
    
    # Extract and validate reviewer state
    reviewer = label('reviewer', {'unloaded', 'requested', 'recovery_required', 'unknown'})
    
    # Extract and validate pending reviews
    pending = status.get('pending_reviews')
    pending = str(pending) if isinstance(pending, int) and pending >= 0 else 'unknown'
    
    # Extract and validate active build ID
    build = status.get('active_build')
    build = ('none' if build is None else build if isinstance(build, str)
             and re.fullmatch(r'br-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}', build) else 'unknown')
    
    # Extract and validate observed_at and compute age
    observed = status.get('observed_at')
    age = 'unknown'
    if isinstance(observed, (int, float)) and math.isfinite(observed) and isinstance(now, (int, float)) and math.isfinite(now) and 0 <= observed <= now:
        seconds = now - observed
        age = f'{seconds:.1f} seconds (' + ('stale' if seconds > 90 else 'fresh') + ')'
    
    # Extract and validate wait_reason
    wait_reason = status.get('wait_reason')
    wait_label = 'none' if wait_reason is None or not isinstance(wait_reason, str) else wait_reason
    valid_wait_reasons = {
        'idle', 'work_in_progress', 'worker_unavailable', 
        'review_recovery_required', 'failed_task_requires_operator', 
        'no_admissible_ready_task', 'needs_clarification', 
        'unchanged_decision_inputs', 'review_evidence_pending'
    }
    if wait_label not in valid_wait_reasons:
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
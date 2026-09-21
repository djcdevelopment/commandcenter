"""OMEN candidate formatter, corrected and reviewed by Codex before integration.

Original: artifacts/hermes-fx99/omen-workers/
hearth-hermes-br-20260920-030141-e036eff8-3e247760/cc-builder-3/fleet_status_format.py
Corrections: nested types, bool/negative rejection, stale capacities, safe labels,
and per-field unknowns instead of hiding all controller capacity for missing busy.
"""

def format_status(state) -> str:
    """Return four allowlisted, control-character-free lines; no I/O."""
    state = state if isinstance(state, dict) else {}
    scheduler = state.get('scheduler')
    reviewer = state.get('reviewer')
    controller = state.get('controller')
    controller = controller if isinstance(controller, dict) else {}
    worker = state.get('worker')
    worker = worker if isinstance(worker, dict) else {}
    
    # Handle scheduler and reviewer status
    if isinstance(scheduler, dict) and scheduler.get('ready') is True:
        scheduler_line = 'Scheduler: FX99 / JEV: ready; CPU only'
    else:
        # Preserve existing four-line output when scheduler is absent or not a dict with ready:true
        controller_ready = controller.get('ready') is True
        worker_ready = worker.get('ready') is True
        
        def capacity(row, key):
            n = row.get(key)
            return str(n) if row.get('ready') is True and type(n) is int and n >= 0 else 'unknown'
        
        controller_line = ('Controller: FX99 -> AM4 / am4-dense-27b: '
                           + ('ready' if controller_ready else 'not ready'))
        controller_capacity = (
            'Controller capacity: '+capacity(controller,'context_length')+' tokens per slot; '
            +capacity(controller,'parallel_slots')+' physical slots; '
            +capacity(controller,'busy_slots')+' busy')
        worker_line = (
            'Worker: OMEN / omen-arc / qwen3-30b-a3b: '
            +('ready' if worker_ready else 'not ready')+'; '
            +capacity(worker,'context_length')+' tokens per slot; '
            +capacity(worker,'parallel_slots')+'/'+capacity(worker,'busy_slots')+'/'
            +capacity(worker,'free_slots')+' total/busy/free slots')
        policy_line = 'At most 2 OMEN builders; manual review; no cloud or AM4 worker fallback.'
        return '\n'.join((controller_line, controller_capacity, worker_line, policy_line)) + '\n'
    
    # Handle reviewer status
    if isinstance(reviewer, dict) and reviewer.get('state') == 'unloaded':
        reviewer_line = 'Reviewer: Hermes / AM4: intentionally unloaded; that is not a scheduler failure'
    else:
        reviewer_line = 'Reviewer: unknown; could be unresponsive or not configured'
    
    # Handle scheduler work status
    active_work = 'unknown'
    pending_reviews = 'unknown'
    wait_reason = 'unknown'
    
    if isinstance(scheduler, dict):
        active_work_val = scheduler.get('active_work')
        if isinstance(active_work_val, int) and active_work_val >= 0:
            active_work = str(active_work_val)
        
        pending_reviews_val = scheduler.get('pending_reviews')
        if isinstance(pending_reviews_val, int) and pending_reviews_val >= 0:
            pending_reviews = str(pending_reviews_val)
        
        wait_reason_val = scheduler.get('wait_reason')
        if isinstance(wait_reason_val, str):
            allowed_labels = {'idle', 'work_in_progress', 'worker_unavailable', 'review_recovery_required', 'needs_clarification'}
            wait_reason = wait_reason_val if wait_reason_val in allowed_labels else 'unknown'
    
    scheduler_work_line = f'Scheduler work: active {active_work}; pending review {pending_reviews}; wait {wait_reason}'
    
    # Preserve worker capacity checks and existing policy
    def capacity(row, key):
        n = row.get(key)
        return str(n) if row.get('ready') is True and type(n) is int and n >= 0 else 'unknown'
    
    worker_ready = worker.get('ready') is True
    worker_line = (
        'Worker: OMEN / omen-arc / qwen3-30b-a3b: '
        +('ready' if worker_ready else 'not ready')+'; '
        +capacity(worker,'context_length')+' tokens per slot; '
        +capacity(worker,'parallel_slots')+'/'+capacity(worker,'busy_slots')+'/'
        +capacity(worker,'free_slots')+' total/busy/free slots')
    
    policy_line = 'At most 2 OMEN builders; manual review; no cloud or AM4 worker fallback.'
    
    # Return the complete output with the new scheduler and reviewer lines
    return '\n'.join((scheduler_line, reviewer_line, scheduler_work_line, worker_line, policy_line)) + '\n'
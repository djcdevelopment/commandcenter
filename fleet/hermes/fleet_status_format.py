"""OMEN candidate formatter, corrected and reviewed by Codex before integration.

Original: artifacts/hermes-fx99/omen-workers/
hearth-hermes-br-20260920-030141-e036eff8-3e247760/cc-builder-3/fleet_status_format.py
Corrections: nested types, bool/negative rejection, stale capacities, safe labels,
and per-field unknowns instead of hiding all controller capacity for missing busy.
"""


def format_status(state) -> str:
    """Return four allowlisted, control-character-free lines; no I/O."""
    state = state if isinstance(state, dict) else {}
    controller = state.get('controller')
    controller = controller if isinstance(controller, dict) else {}
    worker = state.get('worker')
    worker = worker if isinstance(worker, dict) else {}
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
    return '\n'.join((controller_line,controller_capacity,worker_line,policy_line))+'\n'

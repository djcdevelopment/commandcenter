def format_status(state) -> str:
    """Format the fleet status into a 4-line string based on the given state dictionary.

    Args:
        state: A dictionary containing controller, worker, and policy information.

    Returns:
        A string with exactly 4 lines describing the fleet status, ending with a newline.
    """
    if not isinstance(state, dict):
        return "placement/controller model and readiness: unknown\n" \
               "controller context per slot, total physical slots and busy slots: unknown\n" \
               "worker backend/model, readiness, context per slot, total/busy/free slots: unknown\n" \
               "At most 2 OMEN builders; manual review; no cloud or AM4 worker fallback.\n"

    # Extract controller info
    controller = state.get("controller", {})
    controller_ready = controller.get("ready") is True
    controller_model = controller.get("model", "unknown")
    controller_context = controller.get("context_length")
    controller_slots = controller.get("parallel_slots")
    controller_busy = controller.get("busy_slots")

    # Extract worker info
    worker = state.get("worker", {})
    worker_ready = worker.get("ready") is True
    worker_backend = worker.get("backend", "unknown")
    worker_model = worker.get("model", "unknown")
    worker_context = worker.get("context_length")
    worker_slots = worker.get("parallel_slots")
    worker_busy = worker.get("busy_slots")
    worker_free = worker.get("free_slots")

    # Extract policy
    policy = state.get("policy", {})
    max_builders = policy.get("max_builders", "unknown")
    promotion = policy.get("promotion", "unknown")
    fallback = policy.get("fallback", "unknown")

    # Format controller line
    controller_line = f"placement/controller model and readiness: {controller_model} ({'ready' if controller_ready else 'not ready'})"
    
    # Format controller capacity line
    if not controller_ready or not isinstance(controller_context, int) or not isinstance(controller_slots, int) or not isinstance(controller_busy, int):
        controller_capacity = "unknown"
    elif controller_context < 0 or controller_slots < 0 or controller_busy < 0:
        controller_capacity = "unknown"
    else:
        controller_capacity = f"{controller_context} context per slot, {controller_slots} total physical slots, {controller_busy} busy slots"
    
    # Format worker line
    worker_line = f"worker {worker_backend}/{worker_model}, readiness: {'ready' if worker_ready else 'not ready'}, context per slot: {worker_context if isinstance(worker_context, int) and worker_context >= 0 else 'unknown'}, total/busy/free slots: {worker_slots if isinstance(worker_slots, int) and worker_slots >= 0 else 'unknown'}/{worker_busy if isinstance(worker_busy, int) and worker_busy >= 0 else 'unknown'}/{worker_free if isinstance(worker_free, int) and worker_free >= 0 else 'unknown'}"
    
    # Format policy line (fixed)
    policy_line = "At most 2 OMEN builders; manual review; no cloud or AM4 worker fallback."

    # Return complete string with newline
    return (controller_line + "\n" + 
            controller_capacity + "\n" + 
            worker_line + "\n" + 
            policy_line + "\n")
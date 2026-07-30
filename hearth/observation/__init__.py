"""Dispatch observation: turning a completed offload into capability evidence.

This package is the write side of ADR-0027 option B. A `local_generate` dispatch that
carries a caller identity produces one `capacity-observation.v1` artifact and one workflow
event, in the domain corpus under `runs/hearth-offload-<caller>/`, which the belief
projectors then read through `artifact_refs`.

It lives outside `hearth/kernel/` on purpose. ADR-0010 makes the kernel ledger an
audit/telemetry stream and forbids the ledger adapter from becoming "a second birthplace
for domain facts"; birthing the observation in the wrapper that writes audit rows would be
that, under a different name. It also lives outside `hearth/toolsurface/` because it is
not a tool surface — nothing here is callable through the door.

Kernel-free and stdlib-only (plus `tools.workflow`), so `hearth.toolsurface.inference` can
import it without tripping the provider contract.
"""

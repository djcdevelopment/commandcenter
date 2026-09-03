"""Load a side model with the fence, admission, readiness and placement checks in order (ADR-0045 P4).

    fence -> telemetry snapshot -> admission -> trigger load -> wait_ready (health + timings)
          -> read the load report -> assert placement (+ per-BDF commit delta) -> ok
          -> on mismatch: unload, try the sibling entry (``-vk1`` -> ``-vk2``), bounded by ``entries``

Every step emits a receipt row through ``on_event`` so the window's ledger carries the evidence.
The imagegen tenancy fence is READ only (the store's owner literal belongs to that lane); an active
image session refuses the load before anything is touched. Nothing here addresses production ports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

from hearth.rotation.admission import Admission, AdmissionGates, CardState, admit_load
from hearth.rotation.placement import PlacementVerdict, assert_placement, parse_load_report
from hearth.rotation.telemetry import HostTelemetry

FENCE_RESOURCE = "omen-b70-pool"


@dataclass
class LoadResult:
    ok: bool
    model_id: Optional[str]
    entry_used: Optional[str]
    reason: str
    attempts: int = 0
    admission: Optional[Admission] = None
    verdict: Optional[PlacementVerdict] = None
    load_wall_s: Optional[float] = None
    canary_timings: Optional[dict] = None
    events: list = field(default_factory=list)


def default_fence() -> Optional[str]:
    """The active image session id on the B70 pool, or None. Unreadable store -> 'unreadable'."""
    try:
        from hearth.execution.coordination import GpuTenancyStore

        session = GpuTenancyStore().active_image_session(FENCE_RESOURCE)
    except Exception:  # noqa: BLE001
        return "unreadable"
    return session.session_id if session is not None else None


def select_model_log(text: str, model_id: str) -> str:
    """The part of a combined llama-swap /logs text that belongs to ``model_id``.

    llama-swap tags upstream lines with the model id (``[model_id] ...``); when such tags
    exist, exactly those lines are kept -- and only the LAST load's worth, from the final
    device enumeration onward, so a reload's report is not mixed with an earlier one. When
    no line carries the id, the whole text is returned (the parser then fails closed if
    nothing matches).
    """
    if not text:
        return ""
    lines = text.splitlines()
    tagged = [line for line in lines if model_id in line]
    if not tagged:
        return text
    # Keep from the last load-report START onward: the device enumeration or the loader banner
    # (never a "using device" line -- a dual load prints two of those and both must survive).
    starts = [i for i, line in enumerate(tagged)
              if "Vulkan devices" in line or "llama_model_loader" in line]
    if starts:
        tagged = tagged[starts[-1]:]
    return "\n".join(tagged) + "\n"


def cards_from_telemetry(telemetry: HostTelemetry, resident_by_bdf: dict,
                         gates: AdmissionGates, inferring_by_bdf: Optional[dict] = None) -> list:
    """CardState per B70 BDF. Residency comes from the CALLER (catalog + /running), never from
    ``local_committed`` (an activity-window signal -- see telemetry.RESIDENCY_CAVEAT)."""
    inferring_by_bdf = inferring_by_bdf or {}
    cards = []
    for card in telemetry.cards:
        vram = card.dedicated_vram_gb if card.dedicated_vram_gb else gates.card_vram_gb
        temp = card.vram_temp_c if card.vram_temp_c is not None else card.gpu_temp_c
        cards.append(CardState(card.bdf, float(vram), float(resident_by_bdf.get(card.bdf, 0.0)),
                               temp, bool(inferring_by_bdf.get(card.bdf, False))))
    return cards


def load_with_assertion(client, entries: Iterable[str], *, snapshot: Callable[[], HostTelemetry],
                        expected_cards: int, per_card_gb: Optional[float], vram_gb: Optional[float],
                        placement: str, resident_by_bdf: dict,
                        gates: AdmissionGates = AdmissionGates(),
                        fence: Callable[[], Optional[str]] = default_fence,
                        logs: Optional[Callable[[str], str]] = None,
                        on_event: Optional[Callable[[dict], None]] = None,
                        deadline_s: float = 300.0, min_delta_gb: float = 1.0) -> LoadResult:
    entries = [e for e in entries if e]
    result = LoadResult(False, entries[0] if entries else None, None, "not started")

    def emit(step: str, **fields) -> None:
        row = {"ts": datetime.now(timezone.utc).isoformat(), "step": step, **fields}
        result.events.append(row)
        if on_event:
            try:
                on_event(row)
            except Exception:  # noqa: BLE001
                pass

    if not entries:
        result.reason = "no entries to load"
        emit("refused", reason=result.reason)
        return result

    session = fence()
    if session is not None:
        result.reason = ("tenancy store unreadable -- refusing (fail closed)" if session == "unreadable"
                         else f"image session {session} owns {FENCE_RESOURCE}")
        emit("refused", reason=result.reason, fence=session)
        return result
    emit("fence", session=None)

    before = snapshot()
    emit("telemetry", phase="before", commit_free_gb=before.commit_free_gb,
         cards=[c.bdf for c in before.cards], note=before.note)
    cards = cards_from_telemetry(before, resident_by_bdf, gates)
    admission = admit_load(per_card_gb=per_card_gb, vram_gb=vram_gb, placement=placement,
                           cards=cards, commit_free_gb=before.commit_free_gb, gates=gates,
                           model_id=entries[0])
    result.admission = admission
    emit("admission", ok=admission.ok, reason_code=admission.reason_code, card=admission.card_bdf,
         reasons=list(admission.reasons))
    if not admission.ok:
        result.reason = f"admission refused: {admission.reason_code}: {'; '.join(admission.reasons)}"
        return result

    last_reason = "no attempt"
    for entry in entries:
        result.attempts += 1
        result.model_id = entry
        emit("load", entry=entry, attempt=result.attempts)
        outcome = client.wait_ready(entry, deadline_s=deadline_s)
        result.load_wall_s = outcome.wall_s
        result.canary_timings = outcome.canary_timings
        emit("ready", entry=entry, ready=outcome.ready, wall_s=outcome.wall_s,
             first_status=outcome.first_status, error=outcome.error)
        if not outcome.ready:
            last_reason = f"{entry}: not ready within {deadline_s:.0f} s ({outcome.error})"
            client.unload(entry)
            emit("unload", entry=entry, why="not ready")
            continue
        text = logs(entry) if logs else select_model_log(client.logs(), entry)
        report = parse_load_report(text)
        after = snapshot()
        emit("telemetry", phase="after", commit_free_gb=after.commit_free_gb,
             cards=[c.bdf for c in after.cards], note=after.note)
        have_bdf = bool(before.cards) and bool(after.cards)
        verdict = assert_placement(report, expected_cards,
                                   before.local_committed_by_bdf() if have_bdf else None,
                                   after.local_committed_by_bdf() if have_bdf else None,
                                   min_delta_gb=min_delta_gb)
        result.verdict = verdict
        emit("placement", entry=entry, ok=verdict.ok, reason=verdict.reason,
             b70_with_weights=verdict.b70_with_weights, per_card_gb=verdict.per_card_gb,
             bdf_delta_gb=verdict.bdf_delta_gb, corroborated=verdict.bdf_corroborated)
        if verdict.ok:
            result.ok = True
            result.entry_used = entry
            result.reason = verdict.reason
            return result
        last_reason = f"{entry}: {verdict.reason}"
        client.unload(entry)
        emit("unload", entry=entry, why="placement mismatch")
    result.reason = f"no entry placed correctly; last: {last_reason}"
    return result

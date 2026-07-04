"""mode_arbiter — the VRAM estimator + inter-card selection for mechnet modes ("Stream C").

Prototype of the two gating pieces named in the Tempo.ImageGen operator map (2026-07-04):
the img allocator's `TryReserve` takes an OPAQUE per-card byte estimate and answers yes/no
for ONE caller-named card. It leaves to its caller exactly what mechnet modes
(art / local-dev / deep-research / frontier-collab) also need:

  (1) an ESTIMATOR — request/mode -> per-card VRAM bytes, folding in the counter-prompt/CFG
      cost the opaque estimate never modeled ("counter-prompt takes VRAM"); and
  (2) an INTER-CARD SELECTION POLICY — which of the two cards a workload lands on.

Faithful to the arbiter Derek already built (img ADR-0006), and to why:
  * fail CLOSED — reject on any blind or over-budget signal, never guess optimistic;
  * a 16 GiB per-card SAFE ENVELOPE, not the ~32 GiB advertised — pushing past it paged into
    host DDR4 over the 8x PCIe bus (spill) and hung the box twice;
  * "free" is the card's real free-dedicated headroom GIVEN whatever a resident workload
    (oxen) already holds — pin + budget the remainder, never evict.

Pure and IO-free so it is testable without a GPU. It DECIDES; enforcement (process launch,
Job Object kill-floor, spill watchdog) stays in the img allocator. Coefficients are coarse
placeholders to be CALIBRATED against the real batch history in img/data/jobs/*.json.
"""
from __future__ import annotations

from dataclasses import dataclass

GIB = 1024 ** 3
MIB = 1024 ** 2

SAFE_ENVELOPE_BYTES = 16 * GIB    # per-card cap; refuse to trust advertised VRAM
SAFETY_BUFFER_BYTES = 512 * MIB   # matches the img allocator's fixed pad


@dataclass(frozen=True)
class VramModel:
    """Coarse parametric VRAM model. Calibrate against img/data/jobs batch runs."""
    activation_bytes_per_pixel: float = 4.0   # latent + attention activations, per output pixel
    conditioning_bytes: int = 1200 * MIB      # one text-conditioning pass (CLIP/T5 + cross-attn)


def estimate_vram_bytes(base_model_bytes: int, width: int, height: int,
                        cfg_enabled: bool, model: VramModel = VramModel()) -> int:
    """Estimate peak per-card VRAM for one diffusion job.

    The counter-prompt (negative prompt) is real cost: with CFG on, the sampler runs a
    SECOND conditioning pass each step, so the conditioning footprint is counted TWICE.
    Steps are deliberately absent — they cost wall-clock, not peak VRAM (sequential).
    """
    if base_model_bytes < 0 or width <= 0 or height <= 0:
        raise ValueError("base_model_bytes must be >=0 and width/height > 0")
    activation = int(width * height * model.activation_bytes_per_pixel)
    passes = 2 if cfg_enabled else 1
    conditioning = model.conditioning_bytes * passes
    return base_model_bytes + activation + conditioning


@dataclass(frozen=True)
class Card:
    """A physical GPU as the arbiter sees it. `free_dedicated_bytes` is the SYSTEM-WIDE
    free-dedicated figure (sees resident workloads like oxen); < 0 means telemetry is
    blind (fail closed). `known_physical` is False for software/basic-render adapters."""
    luid: str
    free_dedicated_bytes: int
    known_physical: bool = True


@dataclass(frozen=True)
class Admission:
    ok: bool
    card_luid: "str | None"
    reason: str


def admit(estimate: int, card: Card,
          envelope: int = SAFE_ENVELOPE_BYTES,
          buffer: int = SAFETY_BUFFER_BYTES) -> Admission:
    """Fail-closed admission for ONE card — the 5-gate cascade from the img allocator's
    TryReserve, as a decision (no reservation side effect)."""
    if estimate <= 0:
        return Admission(False, None, "insane estimate (<= 0)")
    if estimate > envelope:
        return Admission(False, None,
                         f"estimate {estimate // MIB} MiB exceeds {envelope // GIB} GiB safe envelope")
    if not card.known_physical:
        return Admission(False, None, f"{card.luid} is not a known physical card")
    if card.free_dedicated_bytes < 0:
        return Admission(False, None, f"{card.luid}: no live telemetry (fail closed)")
    if card.free_dedicated_bytes < estimate + buffer:
        return Admission(False, None,
                         f"{card.luid}: insufficient headroom (free {card.free_dedicated_bytes // MIB} MiB "
                         f"< est {estimate // MIB} + buffer {buffer // MIB} MiB)")
    return Admission(True, card.luid, "admitted")


def select_card(cards: "list[Card]", estimate: int,
                envelope: int = SAFE_ENVELOPE_BYTES,
                buffer: int = SAFETY_BUFFER_BYTES) -> Admission:
    """Inter-card policy (the missing piece): among cards that ADMIT, pick the one that
    leaves the MOST headroom after (worst-fit) — maximizing the spill safety margin
    rather than packing tight. Fail closed (no card) if none fits."""
    admissible = []
    for c in cards:
        if admit(estimate, c, envelope, buffer).ok:
            headroom_after = c.free_dedicated_bytes - (estimate + buffer)
            admissible.append((headroom_after, c))
    if not admissible:
        return Admission(False, None, "no card has budget for this job (all rejected)")
    admissible.sort(key=lambda t: t[0], reverse=True)
    headroom_after, best = admissible[0]
    return Admission(True, best.luid, f"selected {best.luid} (headroom-after {headroom_after // MIB} MiB)")


# --- modes: a desired-state over the cards, planned through the arbiter ----------

@dataclass(frozen=True)
class Workload:
    """One GPU tenant in a mode. `pinned_luid` fixes it to a card (a resident backend
    like oxen); None lets the arbiter select. `per_card_estimate` is bytes (from the
    estimator for a transient art job, or the measured resident footprint for an LLM)."""
    name: str
    per_card_estimate: int
    pinned_luid: "str | None" = None


@dataclass(frozen=True)
class Mode:
    """A declared desired-state of the mechnet's GPUs (art / local-dev / deep-research /
    frontier-collab) — the set of workloads that should be resident, and where."""
    name: str
    workloads: "tuple[Workload, ...]"


def plan_mode(mode: Mode, cards: "list[Card]") -> dict:
    """Fail-closed placement plan for a mode. Places each workload in declared order,
    debiting the chosen card's headroom (pin-and-budget-the-remainder, never evict).
    A mode is admissible iff EVERY workload admits. Returns the per-workload decisions
    and whether the whole mode fits — the seed of a safe mode transition."""
    remaining = {c.luid: c.free_dedicated_bytes for c in cards}
    placements = []
    for w in mode.workloads:
        views = [Card(luid, remaining[luid]) for luid in remaining]
        if w.pinned_luid is not None:
            target = next((c for c in views if c.luid == w.pinned_luid), None)
            res = (admit(w.per_card_estimate, target) if target
                   else Admission(False, None, f"pinned card {w.pinned_luid} unknown"))
        else:
            res = select_card(views, w.per_card_estimate)
        placements.append({"workload": w.name, "ok": res.ok,
                           "card": res.card_luid, "reason": res.reason})
        if res.ok:
            remaining[res.card_luid] -= (w.per_card_estimate + SAFETY_BUFFER_BYTES)
    return {"mode": mode.name, "admissible": all(p["ok"] for p in placements),
            "placements": placements}

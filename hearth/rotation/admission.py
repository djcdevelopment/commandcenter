"""Bytes-per-card admission for a model load (ADR-0040 §4 'HEARTH builds only the verified
absences'; ADR-0045 P4). Pure.

Gates ported from ``campaign/qwen38/scripts/server-control.ps1`` (commit floor before/during/after a
load; VRAM-temperature abort) with the campaign's values in ``campaign/qwen38/config/campaign.json``:
``commit_min_free_gb 6.0``, ``vram_temperature_abort_c 95``, ``shared_growth_abort_gb 2.0``; the
scheduler's ``_VRAM_HEADROOM_GB 0.5`` per card. **Fail closed:** any gated quantity that is unknown
(``None``) refuses -- a load is an actuation, and "unmeasured" is not "fine".

Card choice (``choose_target_bdf``): the card with the most free VRAM, ties broken by the cooler
card -- the enforceable form of the rotation program's "the hot card gets the lighter model",
which ADR-0042 made unenforceable as an index rule. The choice is ADVICE for the sibling-entry
order; placement is still asserted after the load.

Co-residency numbers (ADR-0041 W-B 'during'): a resident-but-idle neighbour costs the incumbent 0%;
an actively inferring one on the same card ~8% while it infers. Reported, not gated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional


@dataclass(frozen=True)
class AdmissionGates:
    commit_min_free_gb: float = 6.0
    vram_headroom_gb: float = 0.5
    vram_temperature_abort_c: float = 95.0
    temperature_resume_below_c: float = 80.0
    shared_growth_abort_gb: float = 2.0
    card_vram_gb: float = 32.5


@dataclass(frozen=True)
class CardState:
    bdf: str
    vram_gb: float
    resident_gb: float                 # per-card GB already held (production 14.52 / 15.44)
    temp_c: Optional[float] = None
    inferring: bool = False            # an actively inferring tenant on this card (~8% cost)

    def free_gb(self, headroom_gb: float) -> float:
        return round(self.vram_gb - self.resident_gb - headroom_gb, 3)


@dataclass(frozen=True)
class Admission:
    ok: bool
    reason_code: str                   # ok | commit_floor | vram_fit | thermal | telemetry_unknown | cards_missing | model_unknown
    card_bdf: Optional[str]
    reasons: tuple = ()
    numbers: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EvictionCandidate:
    model_id: str
    bdf: str
    gb: float
    reason: str


def _per_card_need(per_card_gb: Optional[float], vram_gb: Optional[float], placement: str) -> Optional[float]:
    if per_card_gb is not None:
        return float(per_card_gb)
    if vram_gb is not None:
        return float(vram_gb) / (2.0 if placement == "dual" else 1.0)
    return None


def choose_target_bdf(cards: Iterable[CardState], need_gb: float, gates: AdmissionGates) -> Optional[str]:
    """Freest card that fits; ties within 0.25 GB go to the cooler (or non-inferring) card."""
    fitting = [c for c in cards if c.free_gb(gates.vram_headroom_gb) >= need_gb]
    if not fitting:
        return None

    def key(card: CardState):
        temp = card.temp_c if card.temp_c is not None else 999.0
        return (-round(card.free_gb(gates.vram_headroom_gb) / 0.25), card.inferring, temp, card.bdf)

    return sorted(fitting, key=key)[0].bdf


def admit_load(*, per_card_gb: Optional[float], vram_gb: Optional[float], placement: str,
               cards: Iterable[CardState], commit_free_gb: Optional[float],
               gates: AdmissionGates = AdmissionGates(), model_id: str = "") -> Admission:
    """May this model be loaded now, and onto which card (single) or both (dual)?"""
    cards = tuple(cards)
    reasons: list = []
    numbers: dict = {"commit_free_gb": commit_free_gb, "placement": placement,
                     "cards": {c.bdf: {"free_gb": c.free_gb(gates.vram_headroom_gb),
                                       "temp_c": c.temp_c, "inferring": c.inferring} for c in cards}}
    need = _per_card_need(per_card_gb, vram_gb, placement)
    numbers["per_card_need_gb"] = need

    if not cards:
        return Admission(False, "cards_missing", None, ("no B70 telemetry -- nothing to admit onto",), numbers)
    if need is None:
        return Admission(False, "model_unknown", None,
                         (f"{model_id or 'model'} has no measured per-card or total VRAM (nullable stays null; "
                          "an unmeasured model is not admitted onto a production card)",), numbers)
    if commit_free_gb is None:
        return Admission(False, "telemetry_unknown", None, ("commit headroom unknown -- refusing (fail closed)",), numbers)
    if commit_free_gb < gates.commit_min_free_gb:
        return Admission(False, "commit_floor", None,
                         (f"commit free {commit_free_gb:.1f} GB < floor {gates.commit_min_free_gb:.1f} GB",), numbers)
    unknown_temp = [c.bdf for c in cards if c.temp_c is None]
    if unknown_temp:
        return Admission(False, "telemetry_unknown", None,
                         (f"VRAM temperature unknown on {', '.join(unknown_temp)} -- refusing (fail closed)",), numbers)
    hot = [c for c in cards if c.temp_c is not None and c.temp_c >= gates.vram_temperature_abort_c]
    if hot:
        return Admission(False, "thermal", None,
                         tuple(f"{c.bdf} at {c.temp_c:.0f} C >= abort {gates.vram_temperature_abort_c:.0f} C" for c in hot), numbers)

    if placement == "dual":
        short = [c for c in cards if c.free_gb(gates.vram_headroom_gb) < need]
        if len(cards) < 2:
            return Admission(False, "cards_missing", None, ("dual placement needs two B70s",), numbers)
        if short:
            return Admission(False, "vram_fit", None,
                             tuple(f"{c.bdf}: free {c.free_gb(gates.vram_headroom_gb):.2f} GB < need {need:.2f} GB" for c in short), numbers)
        warm = [c for c in cards if c.temp_c is not None and c.temp_c >= gates.temperature_resume_below_c]
        if warm:
            reasons.append("warm card(s): " + ", ".join(f"{c.bdf}@{c.temp_c:.0f}C" for c in warm))
        return Admission(True, "ok", None, tuple(reasons) or ("fits both cards",), numbers)

    target = choose_target_bdf(cards, need, gates)
    if target is None:
        return Admission(False, "vram_fit", None,
                         tuple(f"{c.bdf}: free {c.free_gb(gates.vram_headroom_gb):.2f} GB < need {need:.2f} GB" for c in cards), numbers)
    chosen = next(c for c in cards if c.bdf == target)
    if chosen.inferring:
        reasons.append(f"{target} hosts an actively inferring tenant (~8% while it infers, ADR-0041)")
    if chosen.temp_c is not None and chosen.temp_c >= gates.temperature_resume_below_c:
        reasons.append(f"{target} is warm ({chosen.temp_c:.0f} C)")
    return Admission(True, "ok", target, tuple(reasons) or (f"fits {target}",), numbers)


def eviction_advice(*, resident: Iterable[tuple], need_gb: float, placement: str,
                    cards: Iterable[CardState], gates: AdmissionGates = AdmissionGates(),
                    protected: frozenset = frozenset({"qwen3-30b-a3b"}),
                    in_flight: frozenset = frozenset()) -> list:
    """ADVICE only: which resident side models would have to go for ``need_gb`` to fit.

    ``resident`` = ``(model_id, bdf, gb)`` tuples. Never lists a protected model (production) or one
    with in-flight work (P7: swaps drain, they never cut). Smallest sufficient set per card.
    """
    cards = {c.bdf: c for c in cards}
    advice: list = []
    for bdf, card in cards.items():
        free = card.free_gb(gates.vram_headroom_gb)
        if free >= need_gb:
            continue
        deficit = need_gb - free
        candidates = sorted((r for r in resident if r[1] == bdf and r[0] not in protected and r[0] not in in_flight),
                            key=lambda r: r[2])
        freed = 0.0
        for model_id, _bdf, gb in candidates:
            if freed >= deficit:
                break
            advice.append(EvictionCandidate(model_id, bdf, gb, f"frees {gb:.1f} GB toward a {deficit:.1f} GB deficit on {bdf}"))
            freed += gb
    return advice

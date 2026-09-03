"""Held-out judge panel — a seat may never score the rung or model it sits on.

The 2026-08-29 doc bench scored three arms (gcp-gemini, gcp-gemini-pro, omen-arc)
with a two-seat panel that INCLUDED gcp-gemini: one judge was grading its own
answers, and the arm ranking inverted once that seat was read separately. This
module makes that impossible to do by accident:

    panel = held_out_judges(arms)            # arms = what is being scored
    score_proposal(final, prompt, panel, generate)

``held_out_judges`` takes the arms under test and returns the seats from
``JUDGE_POOL`` that share NEITHER a backend NOR a model with any arm. Fewer
than ``min_seats`` survivors is a ``PanelConflict`` — a panel that quietly
shrank to one judge is the failure mode a panel exists to prevent (see
``matrix.JUDGE_MAX_TOKENS`` for the last time that happened silently).

Sharing a *node* is not a conflict, but it is reported: a judge on ``omen-arc``
scoring an arm that ran on ``omen-swap`` sits on the same two B70s, and an
inferring neighbour on the same card costs the arm ~8% (ADR-0041). That is a
timing confound, not a scoring one, so it rides back as ``panel_note`` rather
than an exclusion — score after the arm's timing rows are captured, never
concurrently.

Pure: no network, no gateway import. The pool declaration is read through
``hearth.toolsurface.backends.load_pool`` (offline TOML) only to resolve an
unpinned ``None`` backend to the door default, to drop retired rungs, and to
map backends to nodes; pass ``pool=`` to inject one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Optional

Seat = tuple[Optional[str], Optional[str]]   # (backend, model); None = unpinned / any

# Candidate judge seats, preference order (a caller capping with ``max_seats``
# takes from the front). Every entry names a rung that is declared, not
# retired, and serves the model without a ceremony:
#   gcp-gemini      strong anchor on trial credits; ALSO a doc-bench arm, so it
#                   is exactly the seat this module drops when that bench runs
#   fx99-ollama     separate host (RTX 2070 SUPER), separate model family;
#                   small, the weaker seat, but genuinely held out from OMEN
#   omen-arc        the door default; usable whenever no arm ran on it — with a
#                   co-residency note whenever an arm ran on the other OMEN rung
#   gcp-gemini-pro  premium reach, thinking model, deliberately last: it is
#                   only worth pinning when the cheaper seats are conflicted
# Seats are validated against the pool at call time, so a retired rung drops
# out with a reason instead of dispatching into a dead endpoint.
JUDGE_POOL: list[tuple[str, str]] = [
    ("gcp-gemini", "gemini-3.5-flash"),
    ("fx99-ollama", "qwen2.5:14b"),
    ("omen-arc", "qwen3-30b-a3b"),
    ("gcp-gemini-pro", "gemini-3.1-pro-preview"),
]

# llama-swap side entries declare the SAME weights twice (``phi4-vk1`` /
# ``phi4-vk2``, ADR-0042) and a dual-card variant (``qwen38-27b-dual``); a
# judge on the sibling entry is still the model judging itself.
_PLACEMENT_SUFFIX = re.compile(r"-(?:vk\d+|dual)$", re.IGNORECASE)


class PanelConflict(ValueError):
    """The requested panel cannot be held out from the arms it would score."""

    def __init__(self, message: str, *, arms: tuple = (), judges: tuple = (),
                 excluded: tuple = (), min_seats: Optional[int] = None) -> None:
        super().__init__(message)
        self.arms = arms
        self.judges = judges
        self.excluded = excluded
        self.min_seats = min_seats


@dataclass(frozen=True)
class Panel:
    """A held-out panel: iterable as ``(backend, model)`` pairs, so it drops
    straight into ``matrix.score_proposal(..., judges=panel, ...)``."""
    judges: tuple[tuple[str, str], ...]
    arms: tuple[Seat, ...]
    excluded: tuple[dict, ...] = ()
    co_resident: tuple[dict, ...] = ()
    panel_note: Optional[str] = None
    notes: tuple[str, ...] = field(default=())

    def __iter__(self) -> Iterator[tuple[str, str]]:
        return iter(self.judges)

    def __len__(self) -> int:
        return len(self.judges)

    def as_dict(self) -> dict:
        return {
            "judges": [list(j) for j in self.judges],
            "arms": [list(a) for a in self.arms],
            "excluded": [dict(e) for e in self.excluded],
            "co_resident": [dict(c) for c in self.co_resident],
            "panel_note": self.panel_note,
            "notes": list(self.notes),
        }


# ---- seat coercion ----------------------------------------------------------
def coerce_seat(item: Any) -> Seat:
    """Normalise an arm/seat spec to ``(backend, model)``.

    Accepts a ``(backend, model)`` pair, a ``{"backend", "model"}`` dict, an
    object with ``backend``/``model`` attributes (``matrix.Role``), or a bare
    backend name string (the doc bench's arm shape) meaning "any model on that
    rung". A pair with a leading ``None`` is an unpinned arm (door default).
    """
    if isinstance(item, str):
        return (item or None, None)
    if isinstance(item, dict):
        return (item.get("backend") or None, item.get("model") or None)
    if isinstance(item, (tuple, list)):
        if len(item) == 2:
            b, m = item
            return (b or None, m or None)
        if len(item) == 3:                     # (label, backend, model) — rejudge_panel shape
            _, b, m = item
            return (b or None, m or None)
        raise ValueError(f"seat must be (backend, model), got {item!r}")
    backend = getattr(item, "backend", None)
    model = getattr(item, "model", None)
    if backend is None and model is None:
        raise ValueError(f"cannot read a (backend, model) seat from {item!r}")
    return (backend or None, model or None)


def _model_key(model: Optional[str]) -> Optional[str]:
    if not model:
        return None
    return _PLACEMENT_SUFFIX.sub("", model.strip().lower())


def _label(seat: Seat) -> str:
    b, m = seat
    return f"{b or '<default>'}/{m or '*'}"


# ---- pool context -----------------------------------------------------------
@dataclass(frozen=True)
class _PoolContext:
    default: Optional[str] = None
    nodes: dict = field(default_factory=dict)      # backend -> node (hardware-identified only)
    retired: frozenset = frozenset()
    declared: frozenset = frozenset()
    available: bool = False


def _pool_context(pool: Any = None) -> _PoolContext:
    """Read what the panel needs from the pool declaration; never raises.

    ``nodes`` only carries backends that declare BOTH ``node`` and
    ``hardware_profile_id`` — co-residency is a statement about shared cards,
    and the cloud rungs deliberately declare no hardware to be co-resident on.
    """
    try:
        if pool is None:
            from hearth.toolsurface.backends import load_pool
            pool = load_pool()
        backends = tuple(pool.backends)
    except Exception:   # missing/invalid pool file: run without pool knowledge
        return _PoolContext()
    nodes: dict[str, str] = {}
    retired: set[str] = set()
    declared: set[str] = set()
    for b in backends:
        declared.add(b.name)
        if getattr(b, "retired", False):
            retired.add(b.name)
        settings = getattr(b, "settings", {}) or {}
        node = settings.get("node")
        if node and settings.get("hardware_profile_id"):
            nodes[b.name] = str(node)
    return _PoolContext(default=getattr(pool, "default", None), nodes=nodes,
                        retired=frozenset(retired), declared=frozenset(declared),
                        available=True)


def _resolve(seat: Seat, ctx: _PoolContext) -> Seat:
    """An unpinned (None) backend actually runs on the door default."""
    b, m = seat
    if b is None and ctx.default:
        return (ctx.default, m)
    return (b, m)


# ---- the rule ---------------------------------------------------------------
def _conflict(seat: Seat, arm: Seat, ctx: _PoolContext) -> Optional[str]:
    """Why ``seat`` may not judge ``arm`` (None = it may)."""
    sb, sm = _resolve(seat, ctx)
    ab, am = _resolve(arm, ctx)
    if sb is not None and ab is not None and sb == ab:
        return f"shares backend {sb!r} with arm {_label(arm)}"
    sk, ak = _model_key(sm), _model_key(am)
    if sk is not None and ak is not None and sk == ak:
        return f"shares model {sm!r} with arm {_label(arm)}"
    return None


def assert_held_out(judges: Iterable[Any], arms: Iterable[Any], *, pool: Any = None) -> None:
    """Raise ``PanelConflict`` if any judge shares a backend or model with any arm.

    ``judges`` and ``arms`` accept anything ``coerce_seat`` reads. Unpinned
    ``None`` backends are resolved to the door default when a pool is readable,
    so an unpinned judge and an unpinned arm are (correctly) the same rung.
    """
    ctx = _pool_context(pool)
    judge_seats = tuple(coerce_seat(j) for j in judges)
    arm_seats = tuple(coerce_seat(a) for a in arms)
    offenders: list[dict] = []
    for seat in judge_seats:
        for arm in arm_seats:
            reason = _conflict(seat, arm, ctx)
            if reason:
                offenders.append({"backend": seat[0], "model": seat[1], "reason": reason})
                break
    if offenders:
        detail = "; ".join(f"{_label((o['backend'], o['model']))} {o['reason']}" for o in offenders)
        raise PanelConflict(
            f"self-judging panel: {detail}. Build the panel with "
            f"held_out_judges(arms) instead of naming seats by hand.",
            arms=arm_seats, judges=judge_seats, excluded=tuple(offenders))


def held_out_judges(arms: Iterable[Any], pool_seats: Optional[Iterable[Any]] = None,
                    min_seats: int = 2, *, max_seats: Optional[int] = None,
                    pool: Any = None) -> Panel:
    """Pick the seats from ``pool_seats`` (default ``JUDGE_POOL``) held out from ``arms``.

    A seat is excluded when it shares a backend OR a model with any arm
    (placement siblings ``x-vk1``/``x-vk2``/``x-dual`` count as one model), or
    when its rung is retired in the pool declaration. Fewer than ``min_seats``
    survivors raises ``PanelConflict`` carrying every exclusion and its reason.
    ``max_seats`` caps the panel from the front of the pool (preference order).

    Surviving seats on the same hardware node as an arm are kept but reported
    in ``co_resident`` and summarised in ``panel_note`` (ADR-0041: an inferring
    neighbour on the same card perturbs the arm's timing — a scheduling
    caveat, not a scoring conflict).
    """
    if min_seats < 1:
        raise ValueError("min_seats must be >= 1")
    ctx = _pool_context(pool)
    arm_seats = tuple(coerce_seat(a) for a in arms)
    if not arm_seats:
        raise ValueError("arms must name at least one (backend, model) under test")
    candidates = [coerce_seat(s) for s in (JUDGE_POOL if pool_seats is None else pool_seats)]

    judges: list[tuple[str, str]] = []
    excluded: list[dict] = []
    for seat in candidates:
        sb, sm = _resolve(seat, ctx)
        if sb is None or sm is None:
            excluded.append({"backend": seat[0], "model": seat[1],
                             "reason": "judge seat must pin both backend and model"})
            continue
        if sb in ctx.retired:
            excluded.append({"backend": sb, "model": sm, "reason": f"backend {sb!r} is retired"})
            continue
        if ctx.available and sb not in ctx.declared:
            excluded.append({"backend": sb, "model": sm,
                             "reason": f"backend {sb!r} is not declared in the pool"})
            continue
        reason = None
        for arm in arm_seats:
            reason = _conflict((sb, sm), arm, ctx)
            if reason:
                break
        if reason:
            excluded.append({"backend": sb, "model": sm, "reason": reason})
            continue
        if (sb, sm) in judges:
            continue
        judges.append((sb, sm))
        if max_seats is not None and len(judges) >= max_seats:
            break

    if len(judges) < min_seats:
        why = "; ".join(f"{_label((e['backend'], e['model']))}: {e['reason']}" for e in excluded) or "empty pool"
        raise PanelConflict(
            f"held-out panel has {len(judges)} seat(s), below min_seats={min_seats} for arms "
            f"[{', '.join(_label(a) for a in arm_seats)}] — excluded {why}. Add held-out "
            f"seats to the pool or lower min_seats deliberately.",
            arms=arm_seats, judges=tuple(judges), excluded=tuple(excluded), min_seats=min_seats)

    co_resident: list[dict] = []
    for jb, jm in judges:
        jnode = ctx.nodes.get(jb)
        if not jnode:
            continue
        for arm in arm_seats:
            ab, _ = _resolve(arm, ctx)
            if ab and ctx.nodes.get(ab) == jnode:
                co_resident.append({"backend": jb, "model": jm, "node": jnode,
                                    "arm": _label(arm)})
                break
    panel_note = None
    if co_resident:
        pairs = ", ".join(f"{c['backend']}/{c['model']} beside {c['arm']} on {c['node']!r}"
                          for c in co_resident)
        panel_note = (f"co-resident seat(s): {pairs}. Same cards as the arm — an inferring "
                      f"neighbour perturbs the arm's timing (ADR-0041); score after the arm's "
                      f"rows are captured, never concurrently. Scores are unaffected.")
    notes: list[str] = []
    if not ctx.available:
        notes.append("pool declaration unreadable: unpinned backends left unresolved, "
                     "retired rungs not filtered")
    return Panel(judges=tuple(judges), arms=arm_seats, excluded=tuple(excluded),
                 co_resident=tuple(co_resident), panel_note=panel_note, notes=tuple(notes))

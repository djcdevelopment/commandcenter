"""``select_next`` — the one place that decides what the backlog runs next.

Pure. No I/O, no clock, no randomness: it takes a scope and a mapping of
already-ordered source iterables and returns the first brief the priority rule
admits, or ``None``. Everything that needs the filesystem (mtimes, promote
timestamps, worth tables) happens in ``sources.py``, which is also what fixes
the ordering WITHIN a source; this module only fixes the order BETWEEN sources.

**Priority: authored > refined > candidate.** A human who wrote a brief out-ranks
a human who promoted a refined intent, which out-ranks a machine-priced
candidate — even when the candidate is worth more. Worth points price the value
of an experiment to the belief corpus; they are not a bid against a person's
stated intent, and letting a 10-point candidate jump an authored brief would
make the queue unpredictable exactly when someone is watching it.

Because each source arrives already ordered, "the first brief the priority rule
admits" is the whole algorithm: authored oldest-first, refined oldest-promotion
first, candidate highest-worth first. Sources are consumed LAZILY — an authored
brief short-circuits the scan, so a full candidate ranking is never built when a
higher rung has work.

``scope`` is a closed enum. An unknown scope raises ValueError rather than
falling back to a default: the scope lives in the arm file, and a typo there
must fail the tick loudly, never silently widen what unattended dispatch may
draw from.
"""
from __future__ import annotations

from typing import Iterable, Mapping, Optional

from hearth.backlog.briefs import SOURCES, Brief

# Priority order, highest first. This tuple IS the rule.
PRIORITY = ("authored", "refined", "candidate")

# scope -> the sources it admits, in priority order.
SCOPES: dict[str, tuple[str, ...]] = {
    "all": PRIORITY,
    "authored": ("authored",),
    "refined": ("refined",),
    "candidate": ("candidate",),
    "authored+refined": ("authored", "refined"),
}


def select_next(scope: str, sources: Mapping[str, Iterable[Brief]]) -> Optional[Brief]:
    """The next brief to dispatch under ``scope``, or None when there is none.

    ``sources`` maps a source name to an iterable of already-ordered briefs; a
    missing key is an empty source. An unknown scope, or an unknown key in
    ``sources``, raises ValueError — both are programming/authoring errors, and
    guessing at either would mean dispatching from a source nobody named.
    """
    if not isinstance(scope, str) or scope not in SCOPES:
        raise ValueError(
            f"scope must be one of {tuple(SCOPES)}; got {scope!r}")
    if sources is None:
        raise ValueError("sources must be a mapping of source name -> briefs")
    for key in sources:
        if key not in SOURCES:
            raise ValueError(f"unknown backlog source {key!r}; known: {SOURCES}")
    for name in SCOPES[scope]:
        candidates = sources.get(name)
        if candidates is None:
            continue
        chosen = next(iter(candidates), None)
        if chosen is not None:
            return chosen
    return None

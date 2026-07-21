"""Real Vertex AI Gemini per-Mtok pricing.

Kept separate from ``hearth.projection.economics``'s ``COST_CLASS_MAP`` on
purpose: that map answers "is this backend sunk or trial" (a HEARTH-internal
taxonomy), while the rates here are Google's own published prices, which are
model-specific and change on Google's schedule, not HEARTH's. Folding real
$/Mtok numbers into economics.py would make a policy module track an external
price list.

OPEN DECISION (flagged in the implementation plan, not resolved here): the
rates below are UNVERIFIED placeholders. Confirm the current published Vertex
AI price for ``gemini-3.5-flash`` and ``gemini-3.1-pro-preview`` before relying
on this module for a real $ decision -- do not guess a number into the table.
Until confirmed, ``cost_usd`` returns ``None`` for these models rather than a
silently wrong dollar figure (the same honest-placeholder convention
``registry/README.md`` already commits to for unverified registry fields).
"""

from __future__ import annotations

from typing import Optional

# USD per 1,000,000 tokens. `None` means "not yet verified" -- see module
# docstring. Fill in real numbers from Google's current Vertex AI pricing page
# before trusting any $ total this module produces.
GEMINI_PRICING_USD_PER_MTOK: dict[str, dict[str, Optional[float]]] = {
    "gemini-3.5-flash": {"input": None, "output": None},
    "gemini-3.1-pro-preview": {"input": None, "output": None},
}

# Matches hearth.projection.economics.COST_CLASS_MAP's existing "sunk"
# classification: these backends have $0 marginal cost, so no pricing lookup
# is needed (or possible -- they aren't priced per-token at all).
SUNK_BACKENDS: frozenset[str] = frozenset({"omen-ollama", "am4-oxen", "am4-moe"})


def cost_usd(backend: Optional[str], model: Optional[str],
             tokens_in: Optional[int], tokens_out: Optional[int]) -> Optional[float]:
    """$ cost of one call, or ``None`` if cost cannot be determined.

    A sunk backend always costs $0 (hardware already paid for). A trial-class
    backend (gcp-gemini, gcp-gemini-pro) is priced by MODEL name -- not
    backend name, since a backend's declared model can change independently --
    against ``GEMINI_PRICING_USD_PER_MTOK``. Returns ``None`` (not 0.0 or a
    guess) whenever the model is unpriced or a token count is missing, so a
    benchmark report can distinguish "known free" from "unknown."
    """
    if backend in SUNK_BACKENDS:
        return 0.0
    rates = GEMINI_PRICING_USD_PER_MTOK.get(model or "")
    if rates is None or tokens_in is None or tokens_out is None:
        return None
    input_rate, output_rate = rates.get("input"), rates.get("output")
    if input_rate is None or output_rate is None:
        return None
    return round((tokens_in * input_rate + tokens_out * output_rate) / 1_000_000.0, 6)

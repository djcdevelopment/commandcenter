"""REFINE — a local refine<->critique convergence loop (pure, injectable).

The loop, one idea in:

    draft = author.expand(idea)
    for round in 1..rounds:
        reviews = [critic.review(draft) for critic in critics]   # >=1
        if every review says CONVERGED: stop        # the critics are satisfied
        draft = author.revise(draft, reviews)       # else fold the critiques in
    final = draft

Every turn is a local model call via ``generate`` — by default
``hearth.toolsurface.inference.local_generate`` (Ollama on OMEN), injected so
tests run fully offline with a scripted fake. Convergence is an explicit,
parseable signal: each critic ends with ``VERDICT: CONVERGED`` or
``VERDICT: REVISE``; a round converges only when EVERY successful critic says
CONVERGED (a missing/garbled verdict is read as REVISE — fail-toward-more-work).

Non-fan default = one critic (the author model self-reviewing, fast). ``fan``
spreads each review round across several local models for diverse perspectives
(qwen + mixtral) at the cost of wall-clock — the hybrid the commander chose.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

DEFAULT_AUTHOR_MODEL = "qwen3-coder:30b"
# Fan critics: qwen for speed + mixtral (bigger MoE) for a different lens. Both
# live on OMEN's ollama, so this is still all-local — just more calls per round.
DEFAULT_FAN_CRITICS = ["qwen3-coder:30b", "mixtral:8x22b-instruct-v0.1-q2_K"]

# Prompt style tuned by the 2026-07 wind-tunnel prompting study
# (MATRIX-WIND-TUNNEL-LOG.html): under fair (length-neutral) judging, a CONCISE author
# is the one small, robust quality win, and a brevity-aware critic avoids the
# over-refinement bloat that a coverage-maximizing critic produces.
AUTHOR_SYSTEM = (
    "You are a systems architect refining a rough idea into a concrete, buildable "
    "proposal. Prize decisiveness and brevity: produce the SHORTEST answer that is "
    "complete and actionable. Lead with the decision or approach. Prefer real "
    "mechanisms over hand-waving; no preamble, no restating the question, minimal "
    "hedging. Use structure (problem / approach / risks / slices) only when it "
    "genuinely aids clarity — not as a template to fill."
)
CRITIC_SYSTEM = (
    "You are a rigorous, skeptical reviewer. Find concrete gaps, unsupported claims, "
    "and real risks — and push the author to CUT scope and hedging, not only to add. "
    "Flag verbosity, over-engineering, and unnecessary complexity as defects. Be "
    "specific and concise; ask for a sharper answer, not a longer one."
)

_EXPAND = (
    "Refine this idea into a concrete design proposal:\n\n{idea}\n\n"
    "Write the full proposal now."
)
_REVISE = (
    "Current proposal:\n\n{draft}\n\n---\nReviewers raised these points:\n\n{reviews}\n\n---\n"
    "Produce an improved version of the proposal that addresses the critiques. "
    "Keep what is already strong; do not restate the critiques. Output only the "
    "revised proposal."
)
_REVIEW = (
    "Review this design proposal (original idea: {idea}). List the concrete issues, "
    "gaps, and specific improvements you would require before it is solid.\n\n"
    "{draft}\n\n---\n"
    "End your review with EXACTLY one final line, either:\n"
    "VERDICT: CONVERGED   (if the proposal is solid and ready)\n"
    "VERDICT: REVISE      (if it needs another pass)"
)

_VERDICT_RE = re.compile(r"VERDICT:\s*(CONVERGED|REVISE)", re.IGNORECASE)


def _parse_verdict(text: str) -> str:
    """CONVERGED only on an explicit CONVERGED verdict; anything else = REVISE."""
    matches = _VERDICT_RE.findall(text or "")
    if matches and matches[-1].upper() == "CONVERGED":
        return "CONVERGED"
    return "REVISE"


def _short(idea: str, n: int = 160) -> str:
    one_line = " ".join(idea.split())
    return one_line if len(one_line) <= n else one_line[: n - 1] + "…"


class _Cost:
    __slots__ = ("author_calls", "critic_calls", "tokens_out", "duration_ms", "failures")

    def __init__(self) -> None:
        self.author_calls = 0
        self.critic_calls = 0
        self.tokens_out = 0
        self.duration_ms = 0
        self.failures = 0

    def add(self, res: dict, *, critic: bool) -> None:
        if critic:
            self.critic_calls += 1
        else:
            self.author_calls += 1
        self.tokens_out += int(res.get("tokens_out") or 0)
        self.duration_ms += int(res.get("duration_ms") or 0)
        if not res.get("ok"):
            self.failures += 1

    def asdict(self) -> dict:
        return {
            "author_calls": self.author_calls,
            "critic_calls": self.critic_calls,
            "tokens_out": self.tokens_out,
            "duration_ms": self.duration_ms,
            "failures": self.failures,
        }


def run_refine(
    idea: str,
    rounds: int = 3,
    fan: bool = False,
    *,
    generate: Optional[Callable[..., dict]] = None,
    author_model: Optional[str] = None,
    author_backend: Optional[str] = None,
    fan_critics: Optional[list[str]] = None,
    critic_specs: Optional[list[tuple]] = None,
    author_system: Optional[str] = None,
    critic_system: Optional[str] = None,
    max_tokens: int = 1500,
    critic_max_tokens: int = 800,
    timeout_s: int = 600,
    on_round: Optional[Callable[[dict], None]] = None,
) -> dict:
    """Run the refine<->critique loop. Pure except for the injected ``generate``.

    Author (planner) calls use ``author_model`` on ``author_backend``; critics
    are ``critic_specs`` = a list of (backend, model) pairs (each reviews every
    round). ``author_backend`` / a per-critic backend route the call to a
    specific HEARTH backend (e.g. "am4-oxen" for the B70s, None for OMEN ollama)
    — this is what lets a matrix cell put the planner on one box and the critic
    on another. Back-compat: if ``critic_specs`` is None it derives from the old
    ``fan`` behavior (fan critics, or the author self-reviewing).

    Returns {ok, idea, final, rounds_run, converged, trail, cost, error}. ``trail``
    is a list of per-round steps {round, draft, reviews:[{model, text, verdict, ok}]}.
    A failed AUTHOR call aborts (ok:false, partial trail preserved); a failed
    CRITIC call is recorded and skipped so one cold model can't sink the round.
    """
    if not isinstance(idea, str) or not idea.strip():
        raise ValueError("idea must be a non-empty string")
    if rounds < 1:
        raise ValueError("rounds must be >= 1")

    if generate is None:  # lazy import keeps this module free of network at import
        from hearth.toolsurface.inference import local_generate as generate

    author_model = author_model or DEFAULT_AUTHOR_MODEL
    a_sys = author_system or AUTHOR_SYSTEM      # per-run prompt-variant overrides
    c_sys = critic_system or CRITIC_SYSTEM
    # Critics as (backend, model) pairs. Explicit critic_specs wins; else derive
    # from the fan flag (fan critics on the default backend, or self-review).
    if critic_specs is not None:
        critic_pairs = [tuple(spec) for spec in critic_specs]
    elif fan:
        critic_pairs = [(None, m) for m in (fan_critics or DEFAULT_FAN_CRITICS)]
    else:
        critic_pairs = [(author_backend, author_model)]

    cost = _Cost()
    idea_short = _short(idea)

    # Initial expand.
    first = generate(_EXPAND.format(idea=idea), model=author_model,
                     backend=author_backend, system=a_sys,
                     max_tokens=max_tokens, timeout_s=timeout_s)
    cost.add(first, critic=False)
    if not first.get("ok"):
        return {"ok": False, "error": f"author expand failed: {first.get('error')}",
                "idea": idea, "final": None, "rounds_run": 0, "converged": False,
                "trail": [], "cost": cost.asdict()}
    draft = first.get("text", "")

    trail: list[dict] = []
    converged = False
    rounds_run = 0

    for r in range(1, rounds + 1):
        rounds_run = r
        reviews: list[dict] = []
        for cb, cm in critic_pairs:
            rv = generate(_REVIEW.format(idea=idea_short, draft=draft), model=cm,
                          backend=cb, system=c_sys, max_tokens=critic_max_tokens,
                          timeout_s=timeout_s)
            cost.add(rv, critic=True)
            if rv.get("ok"):
                text = rv.get("text", "")
                reviews.append({"model": rv.get("model", cm), "text": text,
                                "verdict": _parse_verdict(text), "ok": True})
            else:
                reviews.append({"model": cm, "text": None, "verdict": None,
                                "ok": False, "error": rv.get("error")})

        step = {"round": r, "draft": draft, "reviews": reviews}
        trail.append(step)
        if on_round is not None:
            on_round(step)

        ok_reviews = [rv for rv in reviews if rv["ok"]]
        # Converge only if at least one critic answered AND all who did say CONVERGED.
        if ok_reviews and all(rv["verdict"] == "CONVERGED" for rv in ok_reviews):
            converged = True
            break

        # Fold the critiques back in (author revise). Skip revise if every critic
        # failed this round (nothing to fold, and the models are likely cold).
        if not ok_reviews:
            break
        joined = "\n\n".join(f"[{rv['model']}]\n{rv['text']}" for rv in ok_reviews)
        rev = generate(_REVISE.format(draft=draft, reviews=joined), model=author_model,
                       backend=author_backend, system=a_sys,
                       max_tokens=max_tokens, timeout_s=timeout_s)
        cost.add(rev, critic=False)
        if not rev.get("ok"):
            # Keep the last good draft as final; report the partial run as ok.
            break
        draft = rev.get("text", draft)

    return {"ok": True, "idea": idea, "final": draft, "rounds_run": rounds_run,
            "converged": converged, "trail": trail, "cost": cost.asdict(),
            "error": None}

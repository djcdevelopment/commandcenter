#!/usr/bin/env python3
"""mechnet_exerciser -- two research briefs that exercise the planning/critic loop on the fleet.

DRY-RUN BY DEFAULT. ``--go`` is required before anything reaches the fleet, and Derek
has NOT cued it (2026-09-03: "leverage the dual B70s on OMEN first" was not a dispatch
cue; SCHEDULER-STRATEGY hard rule: never dispatch to fleet builders without his cue).
Without ``--go`` this script opens no SSH session, never calls ``submit_task``, and
writes nothing except stdout (plus ``--manifest <path>`` if you ask for one).

What it exercises -- the brainstorm boxes it advances (plan M6, *builder farm*):

* brief 1 ``planning-loop-rotating-host``: **planning loop -> epic / feature / story**.
  The builder decomposes "the job shop plans a rotating OMEN" into an epic, features and
  stories that each carry ``task_class`` / ``est_tokens`` / mechanically checkable
  acceptance -- the exact fields the Scrum Master (the HEARTH door + the advisory
  scheduler) needs to price and place them.
* brief 2 ``critic-loop-advisory-trust``: **QA: quality grade, risk score**. The builder
  takes the critic seat against one concrete claim about the advisory scheduler and must
  state its rubric before its grade, tie every objection to a real line, and name the one
  experiment that would move the grade.

Both briefs ride the PROVEN revival-probe shape (plan ``hearth-tasklane-revival-probe-936c001e``,
2026-08-29, built on both DEFAULT_BUILDERS, assay 162/162; commit a47abae):

* ``submit_task(prompt, builders=DEFAULT_BUILDERS, plan_id_hint="exerciser-<slug>",
  task_class="research", est_tokens=<int>)`` -- the roster is IMPORTED from
  ``hearth.toolsurface.task_lane`` (never duplicated here: it is live infrastructure and gets
  re-pointed when a rung dies, as it was on 2026-08-29).
* the brief tells the builder it has READ-ONLY source at ``~/commandcenter-src``, must cite
  real paths, may write no production code, and delivers ONE file: ``proposals/<slug>.md``,
  committed alone on its lap branch (the same PREAMBLE/POSTAMBLE discipline as
  ``campaign/pour_speculation.py``).
* ``est_tokens`` is the caller's estimate (``est_tokens_source: caller`` once M3's
  derivation lands in the task lane): ``ceil(len(prompt) / CHARS_PER_TOKEN) + RESEARCH_OUT_TOKENS``.
  It is an input-plus-deliverable size for scheduler hindsight, not a charge.

Run::

    python -m campaign.mechnet_exerciser                    # dry run: prints the plan, dispatches nothing
    python -m campaign.mechnet_exerciser --json             # same, machine-readable
    python -m campaign.mechnet_exerciser --status <plan_id> # read-only: task_status + acceptance check
    python -m campaign.mechnet_exerciser --go               # DISPATCH -- only on Derek's cue
    python -m campaign.mechnet_exerciser --via-door --go    # DISPATCH through the door as mechnet-orchestrator

HOW A VIA-DOOR POUR IS ATTRIBUTED (B-05). Without ``--via-door`` this script calls
``hearth.toolsurface.task_lane.submit_task`` IN-PROCESS: the work reaches the fleet
but the HEARTH ledger never sees a caller, because nothing went through the door.
``--via-door`` routes the same call through ``hearth.callers.client.HearthClient``
instead, so the submit arrives as an MCP ``submit_task`` invocation carrying the
``X-Hearth-Key`` of the MechNet identity:

* ``caller.id = mechnet-orchestrator`` -- the door resolves the key to that caller
  record (minted by OPERATOR_ACTIONS/OPS-06; this repo never holds the secret).
* ``profile = orchestrator`` -- which grants ``dispatch`` (submit_task/task_status)
  and, since 2026-09-06, ``build_request``: the receipt lane a pour is dispatched
  and closed through (hearth/etc/profiles.toml).
* ``task_id = mechnet-exerciser-<UTC date>`` rides the MCP ``_meta``, so every
  event from one pour shares a correlation id in the ledger.

THE KEY IS AN ENVIRONMENT VARIABLE AND ONLY EVER THAT. ``--via-door --go`` reads
it from ``HEARTH_MECHNET_ORCHESTRATOR_KEY``; an unset or empty variable raises a
``ValueError`` naming the VARIABLE before any client is constructed and before any
call is made. The VALUE is never printed, logged, or written to the manifest -- it
is read into one closure and handed to the client, and nothing else in this module
ever sees it. A dry run (``--via-door`` without ``--go``) needs no key at all: it
prints what it would do and calls nothing.

Acceptance once cued (plan M6): ``task_status(plan_id)`` reports ``done`` and
``winner`` in DEFAULT_BUILDERS (cc-builder-2 / cc-builder-3 today); then ONE manual
``python -m hearth.toolsurface.fleet_harvest --sweep --json`` (the ``fleet_harvest`` timer is
held in ``HEARTH_TIMERS_DISABLE``) mirrors the lap branches to origin; ``check_acceptance``
below evaluates the first two mechanically from the ``task_status`` result and lists the
branches the harvest must show on origin. The drain timer stays held (armed, budget-open,
dead candidate backends -- it would dispatch unattended on its first tick); ``runner.json``
cutover is OUT of this window.

VM reachability follow-up -- registered, designed, NOT built (plan 2026-09-03):

* Evidence today (read-only, recorded from the 2026-08-29 sweep; NOT re-attempted this
  window): from cc-builder-2, ``curl http://omen.mshome.net:8081/v1/models`` is unreachable
  because llama-swap (and the direct llama-server on :8082) binds ``127.0.0.1``. The VMs sit
  on the Hyper-V Default Switch (``172.19.240.0/20``, host ``172.19.240.1``), so the local
  builders still decode on the 8 GB fx99 Ollama sidecar instead of the B70 rung.
* Why not just bind ``0.0.0.0`` / ``172.19.240.1``: llama-swap's admin endpoints
  (``/api/models/unload*``) carry no auth, so any VM could unload production; and the Default
  Switch prefix drifts across reboots, so a fixed bind address rots.
* Shape: keep llama-swap on loopback; expose ONLY inference to the VMs through an
  authenticated reverse proxy on ``omen.mshome.net`` (the ``OmenOllamaTracingProxy :11435``
  pattern -- forward ``/v1/*`` to ``:8081`` with the bearer), plus ONE inbound firewall rule
  scoped to the ``vEthernet (Default Switch)`` interface. The firewall rule is an operator
  action for Derek. Only then re-point ``cc-builder-2/3`` ``~/fleet-worker-node/runner.json``
  (backups exist: ``runner.json.bak-2026-08-29``).
* ``VM_REACHABILITY_FOLLOWUP`` below carries the same record for ``--json`` consumers.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hearth.toolsurface.task_expectations import validate_max_age_s  # noqa: E402
from hearth.toolsurface.task_lane import DEFAULT_BUILDERS, submit_task, task_status  # noqa: E402

TASK_CLASS = "research"
PLAN_ID_HINT_PREFIX = "exerciser-"
# ceil(len(prompt) / CHARS_PER_TOKEN) is the usual English-prose approximation;
# RESEARCH_OUT_TOKENS reserves room for a ~1-2 page proposals/<slug>.md deliverable.
CHARS_PER_TOKEN = 4
RESEARCH_OUT_TOKENS = 1500
# The lifetime HEARTH declares for these briefs. A research brief is a single
# model turn plus a commit -- minutes, not hours -- so an hour is generous without
# being an excuse for the coherence watchdog to ignore a wedged run all afternoon
# (task_expectations.MAX_AGE_S_CAP is 7 days; nothing here needs that reach).
RESEARCH_MAX_AGE_S = 3600
DEFAULT_MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "exerciser_manifest.json")

# --- the door identity (B-05) -------------------------------------------------
# The caller record and its profile are policy, not secrets: the NAMES live here
# and in hearth/etc/profiles.toml so a reader can see who a pour is attributed to
# without touching the key registry. The SECRET lives only in the environment.
DOOR_CALLER_ID = "mechnet-orchestrator"
DOOR_PROFILE = "orchestrator"
DOOR_KEY_ENV = "HEARTH_MECHNET_ORCHESTRATOR_KEY"
DOOR_TASK_ID_PREFIX = "mechnet-exerciser-"
# What a --via-door DRY RUN prints. The caller id this script BELIEVES it will be
# is DOOR_CALLER_ID, but belief is not attribution: the door resolves the key to a
# caller record, and only its answer establishes who dispatched. A dry run has not
# asked, so it does not claim.
DRY_RUN_DOOR_LINE = "would dispatch via door as <caller id unknown until the door answers>"
# The exact keyword arguments the door adapter forwards to the door's submit_task.
DOOR_SUBMIT_ARGS = ("prompt", "builders", "plan_id_hint", "task_class", "est_tokens",
                    "requires", "max_age_s")

VM_REACHABILITY_FOLLOWUP = {
    "status": "registered follow-up; designed, not built; not attempted this window",
    "evidence": {
        "probe": "curl http://omen.mshome.net:8081/v1/models  (from cc-builder-2, read-only)",
        "observed": "unreachable: llama-swap (:8081) and llama-server (:8082) bind 127.0.0.1",
        "recorded": "2026-08-29 sweep (commit a47abae); NOT re-attempted 2026-09-03",
        "vm_network": "Hyper-V Default Switch 172.19.240.0/20, host 172.19.240.1 (prefix drifts across reboots)",
        "builders_today": "cc-builder-2/3 decode on fx99-ollama 192.168.12.220:11434 (8 GB sidecar)",
    },
    "why_not_bind_wide": [
        "llama-swap admin endpoints (/api/models/unload*) have no auth: any VM could unload production",
        "the Default Switch prefix drifts across reboots, so a fixed bind address rots",
    ],
    "design": [
        "keep llama-swap on loopback",
        "authenticated reverse proxy on omen.mshome.net exposing ONLY /v1/* -> :8081 with the bearer "
        "(the OmenOllamaTracingProxy :11435 pattern)",
        "ONE inbound firewall rule scoped to the 'vEthernet (Default Switch)' interface -- operator action for Derek",
        "then re-point cc-builder-2/3 ~/fleet-worker-node/runner.json (backups: runner.json.bak-2026-08-29)",
    ],
    "out_of_window": ["runner.json cutover", "0.0.0.0 / 172.19.240.1 listen"],
}


@dataclass(frozen=True)
class Brief:
    """One research brief in the revival-probe shape."""
    slug: str
    title: str
    loop: str          # "planning" | "critic" -- the brainstorm box it exercises
    question: str      # the body between PREAMBLE and POSTAMBLE


PREAMBLE = (
    "MECHNET EXERCISER BRIEF - a planning/critic-loop exercise, NOT a build.\n"
    "You have READ-ONLY source at ~/commandcenter-src. Ground every claim in the\n"
    "ACTUAL code and docs (cite real file paths); do NOT invent files, tools, or\n"
    "APIs. If a file cited below is missing from your checkout, say so under\n"
    "## Open questions instead of guessing. Write NO production code. Produce ONE\n"
    "concise document (~1-2 pages) at proposals/{slug}.md and commit only that\n"
    "file, with sections:\n"
    "  ## Problem  ## Findings (cite real files)  ## Deliverable\n"
    "  ## Risks & tradeoffs  ## Open questions\n"
    "Honor the repo's doctrines: advisory-first (docs/adr/0008-scheduler-advisory-first.md:\n"
    "the scheduler advises, the conductor is the one scheduler), two-economies\n"
    "(metered vs sunk compute), visibility/testability/resilience.\n"
    "Bold-but-grounded beats safe-but-vague.\n\n"
)
POSTAMBLE = (
    "\n\nDeliverable: proposals/{slug}.md only. This is a loop exercise: the assay grades\n"
    "it and the branch is harvested by hand."
)

BRIEFS: tuple[Brief, ...] = (
    Brief(
        slug="planning-loop-rotating-host",
        title="Planning loop: decompose 'the job shop plans a rotating OMEN' into epic / features / stories",
        loop="planning",
        question=(
            "PLANNING-LOOP EXERCISE. Goal under planning: 'the advisory job-shop scheduler "
            "plans model rotation on a stateful host (OMEN: 128 GB RAM, two Arc Pro B70s, "
            "different models loaded and unloaded per task family) and the conductor still "
            "dispatches.' Ground yourself in hearth/scheduler/ontology.py (ModelSpec, "
            "Machine, Job), hearth/scheduler/solve.py (eligibility and the setup terms), "
            "hearth/scheduler/hindsight.py (regret accrual: what the advisory proposal "
            "would have done vs what the conductor did) and hearth/toolsurface/task_lane.py "
            "(submit_task: the CCMETA builders header, DEFAULT_BUILDERS, task_class and "
            "est_tokens). Read docs/adr/0008-scheduler-advisory-first.md for the boundary "
            "no story may cross. Produce ONE epic, 3-5 features and 6-12 stories under "
            "## Deliverable. Every story carries: task_class (build | research | inference), "
            "est_tokens (an integer, and one line on how you estimated it), acceptance "
            "criteria an assay could check mechanically (a test name, a JSON key, a file "
            "path), and the ADR-0008 boundary it must respect. Mark each story as either "
            "'runnable today through submit_task' or 'needs a conductor-side change', and "
            "say why. Cite real paths only."
        ),
    ),
    Brief(
        slug="critic-loop-advisory-trust",
        title="Critic loop: grade the claim 'the advisory scheduler can order the CCMETA eligibility list'",
        loop="critic",
        question=(
            "CRITIC-LOOP EXERCISE (the QA seat: quality grade + risk score). Claim under "
            "review: 'the proposal produced by hearth/scheduler/solve.py is trustworthy "
            "enough that its ordering could be copied into the CCMETA builders header that "
            "hearth/toolsurface/task_lane.py (submit_task) writes, with the conductor still "
            "doing the dispatch (docs/adr/0008-scheduler-advisory-first.md).' Read "
            "hearth/scheduler/solve.py, hearth/scheduler/hindsight.py (the regret record "
            "that would be the evidence for or against), hearth/commander/refine.py (the "
            "existing planner/critic ensemble shape) and hearth/health/gaps.py (what "
            "Watchfire already flags about runs). Under ## Deliverable give: (1) the rubric "
            "you graded with, stated BEFORE the grade -- at least four criteria with "
            "weights; (2) a quality grade A-F and a risk score 0.0-1.0 with the factors "
            "behind each; (3) the three strongest objections to the claim, each tied to a "
            "real function or line; (4) the ONE experiment that would most change your "
            "grade, with the receipt it would leave (a file, a JSON key, a test name). Be "
            "adversarial, not polite; 'insufficient evidence' is an acceptable grade if you "
            "say exactly which evidence is missing."
        ),
    ),
)


def build_prompt(brief: Brief) -> str:
    """The full prompt body for one brief (pure): PREAMBLE + question + POSTAMBLE."""
    return PREAMBLE.format(slug=brief.slug) + brief.question + POSTAMBLE.format(slug=brief.slug)


def estimate_tokens(prompt: str, out_tokens: int = RESEARCH_OUT_TOKENS) -> int:
    """Caller-side est_tokens: prose approximation of the prompt plus the deliverable reserve."""
    return math.ceil(len(prompt) / CHARS_PER_TOKEN) + int(out_tokens)


def plan_submissions(briefs: Iterable[Brief] = BRIEFS,
                     builders: Optional[list[str]] = None,
                     max_age_s: Optional[int] = RESEARCH_MAX_AGE_S) -> list[dict]:
    """What ``--go`` would submit, one dict per brief (pure; nothing dispatched).

    Each entry is exactly the ``submit_task`` call: ``prompt``, ``builders``,
    ``plan_id_hint``, ``task_class``, ``est_tokens``, ``requires``, ``max_age_s``
    -- plus ``slug``/``title``/``loop`` and ``prompt_chars`` for the human reading
    the dry run.

    ``requires`` is the ONE deliverable each brief promises (``proposals/<slug>.md``,
    the same path the PREAMBLE tells the builder to commit), so the harvest side has
    a mechanically checkable expectation instead of a prose instruction. ``max_age_s``
    is the lifetime HEARTH declares for the run; it is validated HERE, by the task
    lane's own ``validate_max_age_s``, so a bad ``--max-age-s`` fails before the plan
    exists rather than one call short of the conductor.
    """
    lifetime = validate_max_age_s(max_age_s)
    roster = list(DEFAULT_BUILDERS) if builders is None else list(builders)
    plan: list[dict] = []
    for brief in briefs:
        prompt = build_prompt(brief)
        deliverable = f"proposals/{brief.slug}.md"
        plan.append({
            "slug": brief.slug,
            "title": brief.title,
            "loop": brief.loop,
            "plan_id_hint": f"{PLAN_ID_HINT_PREFIX}{brief.slug}",
            "builders": roster,
            "task_class": TASK_CLASS,
            "est_tokens": estimate_tokens(prompt),
            "prompt_chars": len(prompt),
            "deliverable": deliverable,
            "requires": [deliverable],
            "max_age_s": lifetime,
            "prompt": prompt,
        })
    return plan


# --- the via-door lane --------------------------------------------------------

def _hearth_client_class():
    """The ``HearthClient`` class, imported LAZILY.

    ``hearth.callers.client`` imports the mcp SDK at module scope, so importing it
    at the top of this file would make the whole exerciser -- including its dry run
    and its tests -- unimportable on an mcp-free interpreter. Keeping the import
    behind a function is the ``hearth/callers/local_caller.py`` idiom, and it is
    also the seam a test substitutes a fake class through, so the via-door path can
    be proven without a socket.
    """
    from hearth.callers.client import HearthClient  # noqa: PLC0415 (deliberately lazy)
    return HearthClient


def door_task_id(today=None) -> str:
    """``mechnet-exerciser-<UTC date>`` -- the MCP ``_meta`` correlation id.

    Dated at CALL time so every event from one pour shares an id that says which
    day the pour happened, rather than a constant baked in when this file was last
    edited.
    """
    day = today or datetime.now(timezone.utc).date()
    return f"{DOOR_TASK_ID_PREFIX}{day.isoformat()}"


def require_door_key(key_env: str = DOOR_KEY_ENV) -> str:
    """Read the door key out of the environment, or raise naming ONLY the variable.

    The raise is the loud failure the ``--via-door`` path is required to make
    BEFORE it constructs a client or makes a call: a missing key must not surface
    as an authentication error from the door, and must never quietly fall back to
    the in-process ``submit_task`` (which would dispatch the pour unattributed --
    exactly the thing --via-door exists to fix).

    The message names the VARIABLE and never its value. The value is returned to
    exactly one caller (``door_submit_fn``/``door_status_fn``), which closes over
    it and hands it to the client; it never enters a report, a manifest, or stdout.
    """
    key = os.environ.get(key_env) or ""
    if not key.strip():
        raise ValueError(
            f"{key_env} is unset or empty: --via-door dispatches as {DOOR_CALLER_ID} "
            f"(profile {DOOR_PROFILE}) and needs that caller's door key in that "
            f"environment variable. OPERATOR_ACTIONS/OPS-06 mints the key and OPS-07 "
            f"stages it; this repo never holds the secret. The value is never printed, "
            f"logged, or written to the manifest -- only the variable NAME is."
        )
    return key


def _client_kwargs(key: str, task_id: str, endpoint: Optional[str] = None) -> dict:
    """Constructor kwargs for ``HearthClient``.

    ``endpoint`` is omitted when not given so the client's own ``DEFAULT_ENDPOINT``
    applies -- the door address is not duplicated here, where it could rot apart
    from ``hearth/callers/client.py``.
    """
    kwargs: dict = {"key": key, "task_id": task_id}
    if endpoint:
        kwargs["endpoint"] = endpoint
    return kwargs


def _door_payload(result: dict) -> Optional[dict]:
    """The tool's own return dict out of an MCP result, or None if unreadable.

    ``structured`` is preferred (it is the tool's return value, already typed);
    a single-key ``{"result": {...}}`` wrapper is unwrapped because that is how a
    non-object return is boxed. ``text`` is the fallback and is only trusted when
    it parses as a JSON object -- a human-readable error string is NOT a payload.
    """
    payload = result.get("structured")
    if isinstance(payload, dict):
        inner = payload.get("result")
        if isinstance(inner, dict) and set(payload) == {"result"}:
            return inner
        return payload
    text = result.get("text")
    if isinstance(text, str) and text.strip():
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def map_door_result(result: object) -> dict:
    """Map one ``HearthClient.call_sync`` result to the dict ``submit_task`` returns.

    The door answers ``{ok, text, structured}``: ``ok`` is transport/tool-level
    (``not isError``) while the tool's OWN ``ok`` lives inside the payload. Both
    have to hold for the caller's ``ok`` to be true, and a failure has to arrive as
    a reported ``{"ok": False, "error": ...}`` row -- the same shape ``run()``
    already renders for an SSH failure -- rather than an exception, so one bad
    brief does not abandon the other mid-pour.
    """
    if not isinstance(result, dict):
        return {"ok": False, "plan_id": None, "builders": [], "inbox_path": None,
                "error": f"door returned {type(result).__name__}, expected a dict"}
    payload = _door_payload(result)
    if payload is None:
        text = result.get("text")
        detail = text.strip() if isinstance(text, str) and text.strip() else \
            "door call returned no readable payload"
        return {"ok": False, "plan_id": None, "builders": [], "inbox_path": None,
                "error": detail}
    mapped = dict(payload)
    if not result.get("ok"):
        mapped["ok"] = False
        mapped.setdefault("error", "door reported the call as an error")
    else:
        mapped.setdefault("ok", True)
    return mapped


def door_submit_fn(key_env: str = DOOR_KEY_ENV, endpoint: Optional[str] = None,
                   task_id: Optional[str] = None) -> Callable[..., dict]:
    """A ``submit_task``-shaped callable that dispatches through the HEARTH door.

    The key is resolved EAGERLY, here, so ``--via-door --go`` with no key in the
    environment fails before any client exists and before any brief is sent. The
    returned closure forwards exactly ``DOOR_SUBMIT_ARGS`` to the door's
    ``submit_task`` and maps the answer back with ``map_door_result``.
    """
    key = require_door_key(key_env)
    client_cls = _hearth_client_class()
    ident = task_id or door_task_id()

    def submit(prompt: str, builders: Optional[list[str]] = None,
               plan_id_hint: Optional[str] = None, task_class: Optional[str] = None,
               est_tokens: Optional[int] = None, requires: Optional[list[str]] = None,
               max_age_s: Optional[int] = None) -> dict:
        client = client_cls(**_client_kwargs(key, ident, endpoint))
        return map_door_result(client.call_sync(
            "submit_task", prompt=prompt, builders=builders,
            plan_id_hint=plan_id_hint, task_class=task_class, est_tokens=est_tokens,
            requires=requires, max_age_s=max_age_s))

    return submit


def door_status_fn(key_env: str = DOOR_KEY_ENV, endpoint: Optional[str] = None,
                   task_id: Optional[str] = None) -> Callable[..., dict]:
    """A ``task_status``-shaped callable that reads through the door.

    ``--status --via-door`` goes through the door too, on purpose: silently reading
    in-process while the caller asked for the door would be a quiet fallback, and
    the read is the same ``dispatch`` capability the submit used, so it proves the
    identity works before a pour rather than after.
    """
    key = require_door_key(key_env)
    client_cls = _hearth_client_class()
    ident = task_id or door_task_id()

    def status(plan_id: str) -> dict:
        client = client_cls(**_client_kwargs(key, ident, endpoint))
        return map_door_result(client.call_sync("task_status", plan_id=plan_id))

    return status


def check_acceptance(status: dict, builders: Optional[Iterable[str]] = None) -> dict:
    """Evaluate plan-M6 acceptance from a ``task_status(plan_id)`` result (pure).

    ok requires: the status call itself ok, the run ``done``, and ``winner`` in the
    builder roster (default DEFAULT_BUILDERS). ``branches`` lists what the manual
    ``fleet_harvest --sweep`` must show on origin (from ``result.builds[*].branch``);
    ``pushed`` records what the conductor already reports pushed. Never raises on a
    partial or odd result -- it explains in ``reasons``.
    """
    roster = set(DEFAULT_BUILDERS if builders is None else builders)
    out = {"ok": False, "done": False, "winner": None, "winner_ok": False,
           "builders_built": [], "branches": [], "pushed": [], "reasons": []}
    if not isinstance(status, dict):
        out["reasons"].append("status is not a dict")
        return out
    if not status.get("ok"):
        out["reasons"].append(f"task_status failed: {status.get('error')}")
        return out
    if not status.get("done"):
        out["reasons"].append("run not finished (no result.json yet)")
        return out
    out["done"] = True
    result = status.get("result")
    if not isinstance(result, dict):
        # task_status(out_file=...) returns only an ACK with a lifted winner.
        result = {"winner": status.get("winner")}
    winner = result.get("winner")
    out["winner"] = winner
    out["winner_ok"] = winner in roster
    if not out["winner_ok"]:
        out["reasons"].append(f"winner {winner!r} not in builders {sorted(roster)}")
    builds = result.get("builds")
    if isinstance(builds, dict):
        for name, row in builds.items():
            if not isinstance(row, dict):
                continue
            out["builders_built"].append(name)
            branch = row.get("branch")
            if branch:
                out["branches"].append(branch)
                if row.get("pushed"):
                    out["pushed"].append(branch)
    out["ok"] = out["done"] and out["winner_ok"]
    if out["ok"]:
        out["reasons"].append("done, winner in roster; run fleet_harvest --sweep once and "
                              "confirm the branches on origin")
    return out


def run_status(plan_id: str, status_fn: Callable[..., dict] = task_status,
               builders: Optional[list[str]] = None) -> dict:
    """Read-only: one ``task_status`` read (SSH to the conductor) plus the acceptance check."""
    status = status_fn(plan_id)
    return {"plan_id": plan_id, "status": status,
            "acceptance": check_acceptance(status, builders)}


def run(go: bool, builders: Optional[list[str]] = None, only: Optional[str] = None,
        submit_fn: Callable[..., dict] = submit_task, via_door: bool = False,
        door_key_env: str = DOOR_KEY_ENV, door_endpoint: Optional[str] = None,
        max_age_s: Optional[int] = RESEARCH_MAX_AGE_S) -> dict:
    """Dry-run (default) renders the plan; ``go=True`` submits each brief via ``submit_fn``.

    ``via_door=True`` REPLACES ``submit_fn`` with the door adapter -- but only
    together with ``go``. A dry run never builds the adapter, so it needs no key
    and makes no call; it just records the intent in the report and prints
    ``DRY_RUN_DOOR_LINE``. The adapter is built BEFORE the plan so a missing key
    fails ahead of everything else.
    """
    briefs = [b for b in BRIEFS if only is None or b.slug == only]
    if only is not None and not briefs:
        raise ValueError(f"unknown brief slug {only!r}; known: {[b.slug for b in BRIEFS]}")
    if via_door and go:
        submit_fn = door_submit_fn(key_env=door_key_env, endpoint=door_endpoint)
    plan = plan_submissions(briefs, builders, max_age_s=max_age_s)
    report = {"dry_run": not go, "task_class": TASK_CLASS, "builders": plan[0]["builders"] if plan else [],
              "count": len(plan), "briefs": [], "vm_reachability_followup": VM_REACHABILITY_FOLLOWUP,
              # Identity is recorded by NAME only. door_key_env is the variable's
              # name, never its value; there is no field anywhere for the value.
              "via_door": bool(via_door),
              "door_caller_id": DOOR_CALLER_ID if via_door else None,
              "door_profile": DOOR_PROFILE if via_door else None,
              "door_key_env": door_key_env if via_door else None,
              "door_endpoint": door_endpoint if via_door else None}
    for item in plan:
        row = {k: item[k] for k in ("slug", "title", "loop", "plan_id_hint", "builders",
                                    "task_class", "est_tokens", "prompt_chars", "deliverable",
                                    "requires", "max_age_s")}
        if not go:
            row["plan_id"] = None
            row["dry_run"] = True
        else:
            res = submit_fn(item["prompt"], builders=item["builders"],
                            plan_id_hint=item["plan_id_hint"], task_class=item["task_class"],
                            est_tokens=item["est_tokens"], requires=item["requires"],
                            max_age_s=item["max_age_s"])
            row.update({"ok": res.get("ok"), "plan_id": res.get("plan_id"),
                        "builders": res.get("builders", item["builders"]),
                        "inbox_path": res.get("inbox_path"), "error": res.get("error")})
        report["briefs"].append(row)
    return report


def _print_human(report: dict) -> None:
    mode = "DRY RUN (nothing dispatched)" if report["dry_run"] else "DISPATCHED"
    print(f"mechnet-exerciser: {mode}  task_class={report['task_class']}  "
          f"builders={report['builders']}")
    for row in report["briefs"]:
        tag = "[dry]" if row.get("dry_run") else ("OK " if row.get("ok") else "ERR")
        print(f"  {tag} {row['slug']:30s} loop={row['loop']:8s} est_tokens={row['est_tokens']:5d} "
              f"prompt_chars={row['prompt_chars']:5d} max_age_s={row['max_age_s']} "
              f"requires={row['requires']} -> {row['deliverable']}"
              + (f"  plan_id={row['plan_id']}" if row.get("plan_id") else "")
              + (f"  error={row['error']}" if row.get("error") else ""))
    if report.get("via_door"):
        # Names only. There is no branch of this function that can print a key.
        if report["dry_run"]:
            print(f"  {DRY_RUN_DOOR_LINE}")
            print(f"  key would be read from ${report['door_key_env']} (value never printed)")
        else:
            print(f"  dispatched via door; key read from ${report['door_key_env']} "
                  f"(value never printed)")
    if report["dry_run"]:
        print("  --go is required to dispatch (Derek's cue; not given 2026-09-03).")
    vm = report["vm_reachability_followup"]
    print(f"vm-reachability follow-up: {vm['status']}")
    print(f"  evidence: {vm['evidence']['probe']} -> {vm['evidence']['observed']}")


def main(argv: Optional[list[str]] = None,
         submit_fn: Callable[..., dict] = submit_task,
         status_fn: Callable[..., dict] = task_status) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True,
                      help="render the plan, dispatch nothing (the default)")
    mode.add_argument("--go", action="store_true",
                      help="DISPATCH both briefs through submit_task -- requires Derek's cue")
    ap.add_argument("--builders", nargs="+", default=None,
                    help=f"override the roster (default: task_lane.DEFAULT_BUILDERS = {DEFAULT_BUILDERS})")
    ap.add_argument("--only", default=None, help="run a single brief by slug")
    ap.add_argument("--status", default=None, metavar="PLAN_ID",
                    help="read-only: task_status(PLAN_ID) + the acceptance check; no dispatch")
    ap.add_argument("--manifest", default=None,
                    help=f"write the report here (default on --go: {DEFAULT_MANIFEST}; dry runs write nothing unless given)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--via-door", action="store_true",
                    help=(f"dispatch (and --status read) through the HEARTH door as "
                          f"{DOOR_CALLER_ID}, keyed from ${DOOR_KEY_ENV}; with --go it "
                          f"replaces the in-process submit_task, without --go it only "
                          f"says so"))
    ap.add_argument("--door-endpoint", default=None,
                    help="override the door endpoint (default: HearthClient.DEFAULT_ENDPOINT)")
    ap.add_argument("--max-age-s", type=int, default=RESEARCH_MAX_AGE_S,
                    help=f"lifetime declared for each brief, in seconds (default {RESEARCH_MAX_AGE_S})")
    args = ap.parse_args(argv)

    if args.status:
        if args.via_door:
            status_fn = door_status_fn(key_env=DOOR_KEY_ENV, endpoint=args.door_endpoint)
        report = run_status(args.status, status_fn=status_fn, builders=args.builders)
        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            acc = report["acceptance"]
            print(f"mechnet-exerciser status {args.status}: done={acc['done']} winner={acc['winner']} "
                  f"ok={acc['ok']}")
            for r in acc["reasons"]:
                print(f"  - {r}")
            if acc["branches"]:
                print(f"  branches to confirm on origin after fleet_harvest --sweep: {acc['branches']}")
        return 0 if acc_ok(report) else 1

    report = run(go=bool(args.go), builders=args.builders, only=args.only, submit_fn=submit_fn,
                 via_door=bool(args.via_door), door_endpoint=args.door_endpoint,
                 max_age_s=args.max_age_s)
    manifest = args.manifest or (DEFAULT_MANIFEST if args.go else None)
    if manifest:
        with open(manifest, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        report["manifest"] = manifest
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_human(report)
        if manifest:
            print(f"manifest -> {manifest}")
    if args.go and any(not r.get("ok") for r in report["briefs"]):
        return 1
    return 0


def acc_ok(status_report: dict) -> bool:
    return bool(status_report.get("acceptance", {}).get("ok"))


if __name__ == "__main__":
    raise SystemExit(main())

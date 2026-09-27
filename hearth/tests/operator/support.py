"""Shared fixtures for the operator suite.

The point of these fixtures is that NOTHING here stubs the thing under test. The
catalog is compiled from the real repository sources, authority is evaluated
against the real `hearth/etc/profiles.toml`, and identity is resolved through the
real `hearth.kernel.auth`. Only two things are faked, because they are the
outside world: the live door and the reachability sweep. Both are faked with the
shapes the real tools actually return.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Optional

from hearth.operator import paths

REPO_ROOT = paths.REPO_ROOT

# Three fixture callers on three real profiles. The keys are fixture strings in
# a temp registry; no deployed key is ever read by a test.
RESEARCH_KEY = "fixture-research-key"
ORCHESTRATOR_KEY = "fixture-orchestrator-key"
UNRESTRICTED_KEY = "fixture-unrestricted-key"

REGISTRY = {
    RESEARCH_KEY: {"id": "fixture-research", "runner_class": "local", "node": "omen",
                   "profile": "research"},
    ORCHESTRATOR_KEY: {"id": "fixture-orchestrator", "runner_class": "local",
                       "node": "omen", "profile": "orchestrator"},
    UNRESTRICTED_KEY: {"id": "fixture-unrestricted", "runner_class": "frontier",
                       "node": "omen", "profile": "unrestricted"},
}

# What capture_resource_snapshot really returns (hearth/toolsurface/scheduler.py).
SERVE_TRUTH = {
    "omen-arc": {"observed_at": "2026-09-17T12:00:00Z", "ready": True,
                 "parallel_slots": 8, "reason": "ready"},
    "omen-swap": {"observed_at": "2026-09-17T12:00:00Z", "ready": False,
                  "loaded_models": [], "reason": "no ready upstream"},
    "fx99-ollama": {"observed_at": "2026-09-17T12:00:00Z", "ready": True,
                    "gpu_placed": True, "loaded_models": ["qwen2.5-coder:7b"],
                    "parallel_slots": 1, "reason": "ready"},
    "am4-ollama": {"observed_at": "2026-09-17T12:00:00Z", "ready": False,
                   "reason": "serve-truth probe failed: URLError"},
}

RUNG_STATE = {"ok": True, "rung": "omen-arc", "verdict": "at_rate",
              "observed_at": "2026-09-17T12:00:00Z", "observed_age_s": 4.2,
              "summary": "at_rate 103 tok/s, 97% of baseline"}

EXECUTION_PROVIDERS = [
    {"name": "omen-arc", "api": "openai", "models": ["qwen3-30b-a3b"],
     "tags": ["default"], "parallel_slots": 8, "node": "omen"},
    {"name": "omen-swap", "api": "openai", "models": ["phi4-vk1"], "tags": [],
     "parallel_slots": 1, "node": "omen"},
]

ROTATION_STATUS = {"endpoint": "http://127.0.0.1:8081", "reachable": True,
                   "running": [], "tenancy": {"image_session": None, "readable": True},
                   "open_windows": [], "catalog_models": ["phi4-vk1"]}

IMAGE_LANES = {"ok": True, "lanes": [], "accepted_lane_count": 2,
               "session": {"state": "idle"}}

# kernel_status carries a `caller` object. It is here on purpose: a test that
# never saw caller data in a door result could not prove the snapshot drops it.
KERNEL_STATUS = {"kernel": "hearth", "repo_root": str(REPO_ROOT),
                 "providers": ["hearth.kernel.gateway#builtin",
                               "hearth.toolsurface.inference"],
                 "ledger_dir": "hearth/var/ledger", "event_count": 12345,
                 "caller": {"id": "fixture-unrestricted", "runner_class": "frontier",
                            "node": "omen"}}

FLEET_PING = {
    "summary": {"total": 2, "up": 1, "down": 1, "offline": 0},
    "nodes": [
        {"name": "omen", "kind": "physical-host", "status": "up", "reachable": True,
         "expect": "up", "purpose": "...",
         "probes": [{"service": "hearth-gateway", "host": "omen-linux",
                     "port": 8710, "reachable": True, "latency_ms": 1.3},
                    {"service": "vllm-router", "host": "omen-linux",
                     "port": 18090, "reachable": False, "latency_ms": None}]},
        {"name": "am4", "kind": "physical-host", "status": "down", "reachable": False,
         "expect": "up", "purpose": "...",
         "probes": [{"service": "ssh", "host": "192.168.12.233", "port": 22,
                     "reachable": False, "latency_ms": None}]},
    ],
}


class FakeDoor:
    """A door that answers with the shapes the real tools return.

    `unavailable` makes one named tool (or every tool) fail the way a down door
    fails, which is how the fault injections are driven.
    """

    def __init__(self, responses: Optional[dict] = None,
                 unavailable: Optional[set[str]] = None,
                 all_down: Optional[str] = None) -> None:
        self.responses = responses if responses is not None else {
            "kernel_status": KERNEL_STATUS,
            "capture_resource_snapshot": SERVE_TRUTH,
            "query_rung_state": RUNG_STATE,
            "list_execution_providers": EXECUTION_PROVIDERS,
            "rotation_status": ROTATION_STATUS,
            "list_image_lanes": IMAGE_LANES,
        }
        self.unavailable = unavailable or set()
        self.all_down = all_down
        self.calls: list[str] = []

    def __call__(self, tool: str, **_kwargs: Any) -> dict:
        self.calls.append(tool)
        if self.all_down:
            return {"ok": False, "tool": tool, "value": None, "error": self.all_down}
        if tool in self.unavailable:
            return {"ok": False, "tool": tool, "value": None,
                    "error": f"timed out after 30s calling {tool}"}
        if tool not in self.responses:
            return {"ok": False, "tool": tool, "value": None,
                    "error": f"no such tool: {tool}"}
        return {"ok": True, "tool": tool, "value": self.responses[tool], "error": None}


def fake_cli_runner(value: Any = FLEET_PING, error: Optional[str] = None):
    def run(argv: list[str], timeout_s: float = 30.0) -> dict:
        if error:
            return {"ok": False, "value": None, "error": error}
        return {"ok": True, "value": value, "error": None}

    return run


def walk_fields(node: Any, path: str = ""):
    """Yield every (path, field object) in a snapshot."""
    if isinstance(node, dict):
        if {"value", "observed_at", "ttl_s", "fresh_until", "source"} <= set(node):
            yield path, node
            return
        for key, value in node.items():
            yield from walk_fields(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk_fields(value, f"{path}[{index}]")


def all_keys(node: Any):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from all_keys(value)
    elif isinstance(node, list):
        for value in node:
            yield from all_keys(value)


# Two human-operator fixtures (D-112): one decides, one requests. Both are
# ephemeral strings in a temporary registry — the real `derek-approver`
# credential is never minted, entered, or looked for by any test.
HUMAN_APPROVER_KEY = "fixture-human-approver-key"
HUMAN_REQUESTER_KEY = "fixture-human-requester-key"

HUMAN_REGISTRY = {
    HUMAN_APPROVER_KEY: {"id": "fixture-approver", "runner_class": "human",
                         "node": "omen", "profile": "human-operator"},
    HUMAN_REQUESTER_KEY: {"id": "fixture-human-requester", "runner_class": "human",
                          "node": "omen", "profile": "human-operator"},
}

SAMPLE_ORCHESTRATOR = {
    "provider": "anthropic",
    "model": "claude-opus",
    "endpoint_or_version": "2026-09",
    "client": "fixture-orchestrator",
    "harness": "cli",
    "session": "fixture-session",
}


class OperatorTestCase(unittest.TestCase):
    """Relocates every operator write into a temp home and mints a temp registry.

    HEARTH_OPERATOR_HOME moves CURRENT.json, the snapshot store, the per-caller
    bundles and the system history; HEARTH_ROOT moves the caller registry. The
    two are deliberately different variables — that separation is what stops a
    worktree run from writing into the deployed tree.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.hearth_root = self.home / "deployed-hearth"
        (self.hearth_root / "var").mkdir(parents=True)
        self.registry = self.hearth_root / "var" / "callers.json"
        self.registry.write_text(json.dumps({**REGISTRY, **HUMAN_REGISTRY}),
                                 encoding="utf-8")
        self._env(paths.OPERATOR_HOME_ENV, str(self.home))
        self._env(paths.HEARTH_ROOT_ENV, str(self.hearth_root))
        self._env(paths.API_KEY_ENV, None)

    # --- shared document fixtures -----------------------------------------
    #
    # Every helper returns a document that validates against its contract, so a
    # test that wants a contract violation has to author one on purpose.

    def child_env(self, **extra: str) -> dict:
        """The environment a subprocess CLI test runs under: the temp state home
        and the temp registry, never a real key."""
        env = dict(os.environ)
        env[paths.OPERATOR_HOME_ENV] = str(self.home)
        env[paths.HEARTH_ROOT_ENV] = str(self.hearth_root)
        env.pop(paths.API_KEY_ENV, None)
        env["PYTHONPATH"] = str(REPO_ROOT)
        env["PYTHONIOENCODING"] = "utf-8"
        env.update(extra)
        return env

    def make_envelope(self, **overrides):
        from hearth.operator import envelope as envelope_mod

        fields = {
            "intent": "Summarize the AM4 capacity note and cite the catalog gaps.",
            "acceptance_criteria": ["cites the catalog", "names the gaps"],
            "inputs": {"repo": "commandcenter", "base_commit": "a" * 40,
                       "paths": ["knowledge/"], "files": []},
            "classification": {},
            "constraints": {"deadline_s": 600, "max_attempts": 3,
                            "max_context_tokens": 32768, "budget": None},
            "submitted_by": "fixture-orchestrator",
            "submission_source": "test",
            "submitted_at": "2026-09-17T12:00:00Z",
        }
        fields.update(overrides)
        return envelope_mod.build_envelope(**fields)

    def capture_snapshot(self, catalog, *, now=None, door=None, cli_runner=None):
        from hearth.operator import canonical, inspection

        moment = now or canonical.parse_rfc3339("2026-09-17T12:00:00Z")
        # The inspection falls back to the in-process probe (hearth.toolsurface.scheduler.
        # capture_resource_snapshot) for any rung the door did not cover; on omen-linux that
        # probe reaches real endpoints (am4-dense native readiness answered from inside a
        # door-down test on 2026-09-27). The tests fake the door, so they fake this too.
        from unittest import mock
        with mock.patch("hearth.toolsurface.scheduler.capture_resource_snapshot",
                        side_effect=RuntimeError("no in-process probe under test")):
            return inspection.capture(catalog, door=door or FakeDoor(),
                                      cli_runner=cli_runner or fake_cli_runner(),
                                      now=moment)

    def make_proposal(self, catalog, snapshot, **overrides):
        """A schema-valid proposal onto the one LIVE direct-inference route."""
        from hearth.operator import authority as authority_mod
        from hearth.operator import proposal as proposal_mod

        fields = {
            "envelope_id": "a" * 64,
            "catalog_version": str(catalog["catalog_version"]),
            "snapshot_id": str(snapshot["snapshot_id"]),
            "snapshot_expires_at": str(snapshot["planning_valid_until"]),
            "orchestrator": dict(SAMPLE_ORCHESTRATOR),
            "policy_version": authority_mod.policy_version(),
            "eligible_routes": [{"route_id": "direct_hearth",
                                 "route_kind": "direct_inference",
                                 "target": "direct_hearth",
                                 # Canonical monetary wire form (WI-G2b): "USD
                                 # <amount>", compared as integer micro-USD.
                                 "estimated_cost": "USD 0.00"}],
            "rejected_routes": [],
            "selected_graph": {"nodes": [{"id": "n1",
                                          "route_kind": "direct_inference",
                                          "target": "direct_hearth",
                                          "inputs": {"envelope_id": "a" * 64},
                                          "expected": {"attempts": 1}}],
                               "edges": []},
            "assumptions": ["omen-arc stays resident for the planning window"],
            "uncertainty": {"confidence": "high", "unknowns": ["decode rate at depth"]},
            "expected": {"time_s": 120, "attempts": 1, "context_tokens": 8192,
                         "resources": ["omen-arc"]},
            "required_authority": ["call_door_generate"],
            "required_approvals": [],
            "rationale": "direct_hearth is the only LIVE implementation of direct_inference.",
        }
        fields.update(overrides)
        return proposal_mod.build_proposal(**fields)

    def _env(self, name: str, value: Optional[str]) -> None:
        previous = os.environ.get(name)

        def restore() -> None:
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous

        self.addCleanup(restore)
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

    def set_key(self, key: Optional[str]) -> None:
        self._env(paths.API_KEY_ENV, key)

    def requester_context(self, key: str):
        """The authenticated requester an approval request binds (D-112 item 2).

        Resolved through the real registry lookup for `key` and minted by
        `hearth.operator.identity`; the ambient key is left as it was, so a test
        that files a request as one caller can still act as another.

        On a candidate that has no authenticated requester context this fails
        with the DEFECT named rather than with an ImportError, so a red run
        against an older candidate says what it is failing on.
        """
        from hearth.operator import identity as identity_mod

        factory = getattr(identity_mod, "authenticated_caller", None)
        if factory is None:
            raise AssertionError(
                "hearth.operator.identity has no authenticated_caller(): this candidate's "
                "approval API takes a caller-supplied requester, so the requester of an "
                "approval is not the authenticated caller (WI-G2b condition 1, "
                "D-112 item 2)")
        previous = os.environ.get(paths.API_KEY_ENV)
        self.set_key(key)
        try:
            return factory(identity_mod.resolve_from_env())
        finally:
            self.set_key(previous)

    # --- cross-candidate harness affordances --------------------------------
    #
    # A frozen regression file is replayed against EARLIER candidates to prove
    # its red (the WI-G2b brief requires exactly that). Where remediation
    # renamed a parameter, a test that hard-codes the new name dies in setup and
    # proves nothing about its own exploit — the false-positive class verifier C
    # found. These two helpers pass whichever spelling the tree under test
    # actually has, so the test reaches its assertion on both trees. They always
    # take the authenticated route on this candidate.

    def approval_requester_fields(self, key: str) -> dict:
        """The requester keyword `request_approval` takes on THIS tree."""
        import inspect

        from hearth.operator import approve as approve_mod

        parameters = inspect.signature(approve_mod.request_approval).parameters
        if "requester" in parameters:
            return {"requester": self.requester_context(key)}
        identity = self.authenticated_identity(key)
        return {"requesting_principal": dict(identity.caller or {})}

    def store_validation_as(self, validation: dict, run_id: str, identity):
        """Store a validation, naming the requester the way THIS tree takes it."""
        import inspect

        from hearth.operator import validate as validate_mod

        parameters = inspect.signature(validate_mod.store_validation).parameters
        if "requester" in parameters:
            return validate_mod.store_validation(validation, run_id, requester=identity)
        return validate_mod.store_validation(validation, run_id,
                                             requesting_principal=dict(identity.caller or {}))

    def authenticated_identity(self, key: str):
        """An identity resolved at the trusted boundary, without disturbing the
        ambient key (what `store_validation(requester=...)` takes)."""
        from hearth.operator.identity import resolve_from_env

        previous = os.environ.get(paths.API_KEY_ENV)
        self.set_key(key)
        try:
            return resolve_from_env()
        finally:
            self.set_key(previous)

"""P10 registration (ADR-0045): the rotation lane is wired into the door's policy
the way every provider must be -- taxonomy, profiles, launcher, guards.

Four boot rules are pinned here, each the thing that would otherwise fail only
when the live gateway restarts:

  1. every rotation tool has exactly one TOOL_CAPABILITY entry, and the
     actuators sit in their own ``rotation_admin`` capability rather than being
     folded into ``summon``/``execution`` (they touch the cards production is
     resident on);
  2. ``rotation_admin`` is granted to ``operator`` and ``unrestricted`` and to
     no other role;
  3. the launcher's ``--providers`` list names the rotation module, every module
     it names is importable, and ``assert_surface_complete`` passes over the
     union of their tools -- the same check ``build_server`` runs at startup;
  4. the knowledge guard accepts the read-side tools' default call shapes
     WITHOUT an EXTRA_KNOWLEDGE_READERS entry, because the guard inspects
     arguments and neither tool takes a path -- so the entry was deliberately
     not added (see the note in gateway.py).

``query_rung_state`` (the rungstate provider, P7b) is registered alongside:
taxonomy entry, launcher list, and the provider contract.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest import TestCase

from hearth.kernel import capabilities as caps
from hearth.kernel.gateway import (EXTRA_KNOWLEDGE_READERS, _task_class_for,
                                   load_providers, wire_knowledge_guards)
from hearth.kernel.guards import GuardStack
from hearth.kernel.ledger import REPO_ROOT
from hearth.rotation.swapclient import DEFAULT_ENDPOINT
from hearth.toolsurface import rotation

HEARTH = Path(__file__).resolve().parents[2]
PROFILES = HEARTH / "etc" / "profiles.toml"
LAUNCHER = HEARTH / "etc" / "start-hearth-gateway.cmd"

ROTATION_MODULE = "hearth.toolsurface.rotation"
RUNGSTATE_MODULE = "hearth.toolsurface.rungstate"
QUERY_TOOLS = ("rotation_status", "recommend_rung")
ADMIN_TOOLS = ("rotation_window", "rotation_load", "rotation_unload",
               "rotation_kv_save", "rotation_kv_restore")
ROTATION_ADMIN = "rotation_admin"


def launcher_providers() -> list[str]:
    """The exact --providers list the HearthGateway scheduled task launches with.

    Read as bytes: the launcher is a DOS batch file with mixed line endings and
    must never be rewritten by a text-mode round trip."""
    text = LAUNCHER.read_bytes().decode("utf-8", errors="replace")
    match = re.search(r"--providers\s+(\S+)", text)
    assert match, "start-hearth-gateway.cmd has no --providers list"
    return match.group(1).split(",")


class TaxonomyTests(TestCase):
    def test_every_rotation_tool_is_classified(self) -> None:
        names = [tool.__name__ for tool in rotation.get_tools()]
        self.assertEqual(sorted(names), sorted(QUERY_TOOLS + ADMIN_TOOLS),
                         "the provider's surface drifted from what P10 registered")
        for name in QUERY_TOOLS:
            with self.subTest(tool=name):
                self.assertEqual(caps.capability_for(name), "query")
        for name in ADMIN_TOOLS:
            with self.subTest(tool=name):
                self.assertEqual(caps.capability_for(name), ROTATION_ADMIN)

    def test_actuators_are_not_folded_into_an_existing_capability(self) -> None:
        """rotation_admin is its own name in the taxonomy, so the loader will
        reject a profiles.toml that misspells it and a profile cannot pick the
        actuators up by inheriting summon/execution."""
        self.assertIn(ROTATION_ADMIN, caps.KNOWN_CAPABILITIES)
        self.assertEqual(caps.authority_for(ROTATION_ADMIN), caps.AUTH_GATEWAY)

    def test_query_rung_state_is_classified_as_query(self) -> None:
        self.assertEqual(caps.capability_for("query_rung_state"), "query")

    def test_surface_complete_over_the_rotation_provider(self) -> None:
        caps.assert_surface_complete(t.__name__ for t in rotation.get_tools())


class ProfileTests(TestCase):
    def setUp(self) -> None:
        self.profiles = caps.load_profiles(PROFILES)

    def test_rotation_admin_granted_to_operator_and_unrestricted_only(self) -> None:
        granted = sorted(name for name, profile in self.profiles.items()
                         if profile.grants(ROTATION_ADMIN))
        self.assertEqual(granted, ["operator", "unrestricted"])

    def test_operator_reaches_every_actuator(self) -> None:
        for name in ADMIN_TOOLS:
            with self.subTest(tool=name):
                allowed, capability = caps.check_tool_access(self.profiles["operator"], name)
                self.assertTrue(allowed)
                self.assertEqual(capability, ROTATION_ADMIN)

    def test_research_reads_but_cannot_actuate(self) -> None:
        research = self.profiles["research"]
        for name in QUERY_TOOLS + ("query_rung_state",):
            with self.subTest(tool=name):
                allowed, _ = caps.check_tool_access(research, name)
                self.assertTrue(allowed, f"research should reach {name} via query")
        for name in ADMIN_TOOLS:
            with self.subTest(tool=name):
                allowed, _ = caps.check_tool_access(research, name)
                self.assertFalse(allowed, f"research must not reach {name}")

    def test_unprofiled_caller_denied_every_rotation_tool(self) -> None:
        for name in QUERY_TOOLS + ADMIN_TOOLS:
            with self.subTest(tool=name):
                allowed, _ = caps.check_tool_access(None, name)
                self.assertFalse(allowed)


class LauncherTests(TestCase):
    """The boot rules build_server() enforces, run against the launcher's real
    provider list without standing up a server."""

    def test_launcher_names_the_rotation_and_rungstate_providers(self) -> None:
        spec = launcher_providers()
        self.assertIn(ROTATION_MODULE, spec)
        self.assertIn(RUNGSTATE_MODULE, spec)

    def test_every_launcher_provider_loads_and_the_surface_is_complete(self) -> None:
        spec = launcher_providers()
        providers = load_providers(",".join(spec))
        # load_providers skips an unimportable module with a warning; the live
        # door would then boot WITHOUT it, so the launcher must only name
        # modules that actually load.
        self.assertEqual(sorted(providers), sorted(spec),
                         "a --providers module was skipped at load")
        names = [t.__name__ for tools in providers.values() for t in tools]
        self.assertEqual(len(names), len(set(names)), "duplicate tool names across providers")
        caps.assert_surface_complete(names)
        for name in QUERY_TOOLS + ADMIN_TOOLS + ("query_rung_state",):
            self.assertIn(name, names)
        caps.load_profiles(PROFILES)


class GuardTests(TestCase):
    """EXTRA_KNOWLEDGE_READERS was deliberately NOT widened for the read-side
    tools: the guard inspects arguments, and neither takes a path argument."""

    def _guards(self) -> GuardStack:
        guards = GuardStack(repo_root=REPO_ROOT)
        providers = load_providers(f"{ROTATION_MODULE},hearth.toolsurface.knowledge")
        wire_knowledge_guards(guards, providers)
        return guards

    def test_read_side_tools_pass_the_guard_unlisted(self) -> None:
        self.assertNotIn("rotation_status", EXTRA_KNOWLEDGE_READERS)
        self.assertNotIn("recommend_rung", EXTRA_KNOWLEDGE_READERS)
        self.assertNotIn("query_rung_state", EXTRA_KNOWLEDGE_READERS)
        guards = self._guards()
        # FastMCP passes default argument values, so these are the shapes the
        # guard actually sees on a bare call.
        guards.check("rotation_status", {"endpoint": DEFAULT_ENDPOINT})
        guards.check("recommend_rung", {"task_family": "summarization", "prompt_bytes": 0})
        guards.check("query_rung_state", {"rung": "omen-arc"})

    def test_guard_still_refuses_a_rotation_tool_handed_a_knowledge_path(self) -> None:
        """If a rotation tool ever grows a path parameter, this is the refusal
        the door will produce until a reader entry is added on purpose."""
        from hearth.kernel.guards import GuardRejection
        with self.assertRaises(GuardRejection):
            self._guards().check("rotation_status", {"endpoint": "knowledge/omen_catalog.json"})


class TaskClassTests(TestCase):
    def test_read_side_tools_ledger_as_health(self) -> None:
        self.assertEqual(_task_class_for("rotation_status"), "health")
        # exact match wins over the query_ prefix rule
        self.assertEqual(_task_class_for("query_rung_state"), "health")

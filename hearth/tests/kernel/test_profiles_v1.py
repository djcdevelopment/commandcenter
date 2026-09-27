"""ADR-0023: authority is granted by naming a role, never by omitting one.

Two things are pinned here. First the inversion itself — an unprofiled caller is
denied, where before 2026-07-20 it reached all 47 tools. Second, and less
obvious: that `unrestricted` stays complete as the taxonomy grows. It is written
out capability-by-capability rather than wildcarded, so widening the taxonomy
cannot silently widen the role — but the same property means a NEW capability
would silently *narrow* it, and the first symptom would be the frontier operator
being denied a tool it just added. This test turns that into a red build.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest import TestCase

from hearth.kernel import capabilities as caps

PROFILES = Path(__file__).resolve().parents[2] / "etc" / "profiles.toml"


class UnprofiledIsDeniedTests(TestCase):
    def test_no_profile_is_denied_every_tool(self) -> None:
        """The inversion. Sampled across authority domains so a partial
        regression (e.g. only filesystem re-opening) cannot slip through."""
        for tool in ("read_file", "write_file", "git_diff", "git_commit_push",
                     "local_generate", "submit_task", "kernel_change",
                     "kernel_status", "run_tests", "record_event"):
            with self.subTest(tool=tool):
                allowed, _ = caps.check_tool_access(None, tool)
                self.assertFalse(
                    allowed, f"unprofiled caller must be denied {tool}")

    def test_unknown_tool_also_denied_for_unprofiled(self) -> None:
        allowed, _ = caps.check_tool_access(None, "no_such_tool_exists")
        self.assertFalse(allowed)

    def test_ledger_label_marks_the_caller_as_unauthorized(self) -> None:
        """The label rides every event, so it must not read as a grant.
        'legacy-unrestricted' described the old semantics and would now be an
        actively misleading record of a denied call."""
        self.assertEqual(caps.LEGACY_PROFILE, "unprofiled")
        self.assertNotIn("unrestricted", caps.LEGACY_PROFILE)


class UnrestrictedProfileTests(TestCase):
    def setUp(self) -> None:
        self.profiles = caps.load_profiles(PROFILES)

    def test_unrestricted_covers_the_entire_taxonomy_minus_approve(self) -> None:
        """D-112: `approve` is withheld from `unrestricted` on purpose — the self-approval
        boundary. Agents (even frontier agents holding unrestricted) may not approve
        human-gated routes. Every other capability must be granted."""
        every = {c for c in caps.TOOL_CAPABILITY.values() if c}
        expected = every - {"approve"}
        missing = sorted(c for c in expected
                         if not self.profiles["unrestricted"].grants(c))
        self.assertEqual(
            missing, [],
            f"[profile.unrestricted] is missing {missing} — a new capability "
            f"silently narrowed the role it is supposed to keep complete")
        self.assertFalse(
            self.profiles["unrestricted"].grants("approve"),
            "[profile.unrestricted] must not grant `approve` (D-112 approval boundary)")

    def test_unrestricted_reaches_every_mounted_tool_except_operator_approve(self) -> None:
        for tool in caps.TOOL_CAPABILITY:
            allowed, _ = caps.check_tool_access(self.profiles["unrestricted"], tool)
            if tool == "operator_approve":
                self.assertFalse(allowed, f"unrestricted must NOT reach {tool} (D-112)")
            else:
                self.assertTrue(allowed, f"unrestricted must reach {tool}")


class OrchestratorProfileTests(TestCase):
    """The role `mechnet-orchestrator` is assigned (2026-09-06, OPS-06).

    An identity that can START a long-running pour but not CLOSE its receipt
    leaves an open build request nobody holds, so `build_request` belongs to the
    role that dispatches the pour — not only to `operator` above it.
    """

    def setUp(self) -> None:
        self.profiles = caps.load_profiles(PROFILES)

    def test_orchestrator_grants_the_receipt_lane(self) -> None:
        self.assertTrue(self.profiles["orchestrator"].grants("build_request"))
        for tool in ("create_build_request", "get_build_request", "list_build_requests",
                     "update_build_request", "execute_build_request", "close_build_request"):
            with self.subTest(tool=tool):
                allowed, capability = caps.check_tool_access(self.profiles["orchestrator"], tool)
                self.assertTrue(allowed, f"orchestrator should reach {tool}")
                self.assertEqual(capability, "build_request")

    def test_orchestrator_keeps_what_it_already_had(self) -> None:
        """A new grant must not be a rewrite. dispatch/queue/schedule/harvest come
        from the role itself, read/generate from the inherited chain."""
        for capability in ("dispatch", "queue", "schedule", "harvest",
                           "test", "write", "repo_content", "repo_write",
                           "read", "query", "generate", "execution", "status",
                           "repo_metadata"):
            with self.subTest(capability=capability):
                self.assertTrue(self.profiles["orchestrator"].grants(capability))

    def test_orchestrator_still_stops_short_of_the_operator_console(self) -> None:
        """Widening one capability must not quietly promote the role. These are
        operator-and-above grants and must stay outside orchestrator."""
        for capability in ("kernel_admin", "rotation_admin", "summon", "health",
                           "commander", "dream", "knowledge_write", "catalog_write",
                           "image_generate", "image_session_admin", "media_generate",
                           "media_render"):
            with self.subTest(capability=capability):
                self.assertFalse(self.profiles["orchestrator"].grants(capability))

    def test_the_duplicate_grant_across_inheritance_is_accepted(self) -> None:
        """`operator` declares `build_request` DIRECTLY and now also inherits it
        from `orchestrator`. That duplicate is a set union in `_resolve`, not an
        error — this pins that reading, because the alternative (deleting the
        operator line) would make operator's grant depend on a parent it does not
        control. Asserted against the raw TOML so it cannot pass by accident once
        the direct declaration is gone."""
        with PROFILES.open("rb") as handle:
            raw = tomllib.load(handle)["profile"]
        self.assertIn("build_request", raw["operator"]["capabilities"])
        self.assertIn("build_request", raw["orchestrator"]["capabilities"])
        self.assertEqual(raw["operator"]["inherits"], "orchestrator")
        # The loader takes the file as it stands — no exception, both roles resolved.
        profiles = caps.load_profiles(PROFILES)
        self.assertTrue(profiles["operator"].grants("build_request"))
        self.assertTrue(profiles["orchestrator"].grants("build_request"))


class OperatorProfileTests(TestCase):
    def setUp(self) -> None:
        self.profiles = caps.load_profiles(PROFILES)

    def test_operator_withholds_only_privileged_resource_controls(self) -> None:
        """The operator boundary is a set of deliberate exclusions.

        Originally one: an operator acts THROUGH the door, it does not
        reconfigure the door (`kernel_admin`).

        Since 2026-08-25 there is a second, recorded in ADR docs/adr#0035:
        `media_render` leases a GPU media engine and WRITES PROMOTED MEDIA into
        the drafts tree the review UI treats as authoritative. That is not a
        read-only console action, so authority stays narrow until an actual
        operator workflow needs it -- widening later is a one-line profile
        change, narrowing after the fact is a revocation.

        If this test starts failing because a THIRD capability was withheld,
        that is a real policy change and belongs in an ADR, not a quiet edit.
        Do not "fix" a failure here by granting the capability."""
        every = {c for c in caps.TOOL_CAPABILITY.values() if c}
        withheld = sorted(c for c in every
                          if not self.profiles["operator"].grants(c))
        # D-112: `approve` is reserved for `human-operator` and withheld from `operator`.
        self.assertEqual(withheld, ["approve", "image_session_admin", "kernel_admin", "media_render"])

    def test_operator_cannot_change_the_kernel(self) -> None:
        allowed, _ = caps.check_tool_access(self.profiles["operator"], "kernel_change")
        self.assertFalse(allowed)

    def test_operator_can_still_do_the_work(self) -> None:
        for tool in ("local_generate", "submit_task", "run_tests", "git_commit_push",
                     "write_file", "close_build_request", "wake_am4", "record_event"):
            with self.subTest(tool=tool):
                allowed, _ = caps.check_tool_access(self.profiles["operator"], tool)
                self.assertTrue(allowed, f"operator should reach {tool}")


class HumanOperatorProfileTests(TestCase):
    """The interactive human operator role (D-112). Holds approve, status, query.
    Inherits nothing."""

    def setUp(self) -> None:
        self.profiles = caps.load_profiles(PROFILES)

    def test_human_operator_grants_exact_capabilities(self) -> None:
        profile = self.profiles["human-operator"]
        self.assertEqual(profile.capabilities, frozenset({"approve", "status", "query"}))

    def test_human_operator_can_reach_operator_approve(self) -> None:
        profile = self.profiles["human-operator"]
        allowed, capability = caps.check_tool_access(profile, "operator_approve")
        self.assertTrue(allowed)
        self.assertEqual(capability, "approve")


class ApproveIsHeldOnlyByHumanOperatorTests(TestCase):
    """D-112 item 1, as amended: taxonomy tests must prove that NO other profile
    obtains `approve` directly, through inheritance, wildcard expansion, or
    fallback behaviour.

    The WI-G2 verification found the boundary sound but proven only for
    `unrestricted`; these assertions close the whole roster, the inheritance
    chains, and the two fallback paths (no profile, unknown profile).
    """

    def setUp(self) -> None:
        self.profiles = caps.load_profiles(PROFILES)
        with PROFILES.open("rb") as handle:
            self.raw = tomllib.load(handle)["profile"]

    def test_only_human_operator_holds_approve_anywhere_in_the_roster(self) -> None:
        holders = sorted(name for name, profile in self.profiles.items()
                         if profile.grants("approve"))
        self.assertEqual(holders, ["human-operator"])

    def test_no_other_profile_declares_approve_directly_in_the_file(self) -> None:
        declared = sorted(name for name, table in self.raw.items()
                          if "approve" in (table.get("capabilities") or []))
        self.assertEqual(declared, ["human-operator"])

    def test_approve_is_not_reachable_through_any_inheritance_chain(self) -> None:
        """`human-operator` inherits nothing, so no chain can carry `approve`
        into another role."""
        self.assertIsNone(self.raw["human-operator"].get("inherits"))
        for name, table in self.raw.items():
            chain, cursor = [], table.get("inherits")
            while cursor:
                self.assertNotIn(cursor, chain, f"inheritance cycle at {name}")
                chain.append(cursor)
                cursor = self.raw[cursor].get("inherits")
            with self.subTest(profile=name):
                self.assertNotIn("human-operator", chain)

    def test_approve_is_mapped_only_to_operator_approve(self) -> None:
        mapped = sorted(tool for tool, capability in caps.TOOL_CAPABILITY.items()
                        if capability == "approve")
        self.assertEqual(mapped, ["operator_approve"])

    def test_no_profile_but_human_operator_reaches_operator_approve(self) -> None:
        for name, profile in self.profiles.items():
            allowed, capability = caps.check_tool_access(profile, "operator_approve")
            with self.subTest(profile=name):
                if name == "human-operator":
                    self.assertTrue(allowed)
                    self.assertEqual(capability, "approve")
                else:
                    self.assertFalse(allowed)

    def test_the_fallback_paths_do_not_grant_approve(self) -> None:
        """An absent profile is not a wide one, and neither is an unknown name."""
        allowed, _ = caps.check_tool_access(None, "operator_approve")
        self.assertFalse(allowed)
        self.assertNotIn("no-such-profile", self.profiles)
        self.assertIsNone(self.profiles.get("no-such-profile"))

    def test_no_wildcard_expansion_exists_in_the_policy_file(self) -> None:
        """`unrestricted` is written out capability-by-capability on purpose: a
        wildcard would silently pick `approve` up as the taxonomy grows."""
        for name, table in self.raw.items():
            with self.subTest(profile=name):
                self.assertNotIn("*", table.get("capabilities") or [])
                self.assertNotIn("all", table.get("capabilities") or [])


class RosterTests(TestCase):
    """Every role named in policy must exist, so an assignment cannot reference
    a profile that was renamed out from under it."""

    def test_v1_roles_all_resolve(self) -> None:
        profiles = caps.load_profiles(PROFILES)
        for name in ("research", "generation-proxy", "builder", "orchestrator",
                      "operator", "irc-adapter", "imagegen-client", "imagegen-admin",
                      "unrestricted", "human-operator"):
            self.assertIn(name, profiles)

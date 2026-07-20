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

    def test_unrestricted_covers_the_entire_taxonomy(self) -> None:
        """If this fails you added a capability without deciding whether the
        frontier operator gets it. Add it to [profile.unrestricted] (almost
        always) or document why that identity is excluded."""
        every = {c for c in caps.TOOL_CAPABILITY.values() if c}
        missing = sorted(c for c in every
                         if not self.profiles["unrestricted"].grants(c))
        self.assertEqual(
            missing, [],
            f"[profile.unrestricted] is missing {missing} — a new capability "
            f"silently narrowed the role it is supposed to keep complete")

    def test_unrestricted_reaches_every_mounted_tool(self) -> None:
        for tool in caps.TOOL_CAPABILITY:
            allowed, _ = caps.check_tool_access(self.profiles["unrestricted"], tool)
            self.assertTrue(allowed, f"unrestricted must reach {tool}")


class OperatorProfileTests(TestCase):
    def setUp(self) -> None:
        self.profiles = caps.load_profiles(PROFILES)

    def test_operator_withholds_exactly_kernel_admin(self) -> None:
        """The operator boundary is a single deliberate exclusion: an operator
        acts THROUGH the door, it does not reconfigure the door. If this test
        starts failing because a second capability was withheld, that is a real
        policy change and belongs in an ADR, not in a quiet edit."""
        every = {c for c in caps.TOOL_CAPABILITY.values() if c}
        withheld = sorted(c for c in every
                          if not self.profiles["operator"].grants(c))
        self.assertEqual(withheld, ["kernel_admin"])

    def test_operator_cannot_change_the_kernel(self) -> None:
        allowed, _ = caps.check_tool_access(self.profiles["operator"], "kernel_change")
        self.assertFalse(allowed)

    def test_operator_can_still_do_the_work(self) -> None:
        for tool in ("local_generate", "submit_task", "run_tests", "git_commit_push",
                     "write_file", "close_build_request", "wake_am4", "record_event"):
            with self.subTest(tool=tool):
                allowed, _ = caps.check_tool_access(self.profiles["operator"], tool)
                self.assertTrue(allowed, f"operator should reach {tool}")


class RosterTests(TestCase):
    """Every role named in policy must exist, so an assignment cannot reference
    a profile that was renamed out from under it."""

    def test_v1_roles_all_resolve(self) -> None:
        profiles = caps.load_profiles(PROFILES)
        for name in ("research", "generation-proxy", "builder", "orchestrator",
                     "operator", "unrestricted"):
            self.assertIn(name, profiles)

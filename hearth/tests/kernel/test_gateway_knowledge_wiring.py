"""Regression: patrol's capacity_path arg names a knowledge/ path (read-only)
and must be trusted by the guard the same way the knowledge module's own
tools are — see EXTRA_KNOWLEDGE_READERS in hearth.kernel.gateway."""

import unittest

from hearth.kernel.gateway import load_providers, wire_knowledge_guards
from hearth.kernel.guards import GuardRejection, GuardStack
from hearth.kernel.ledger import REPO_ROOT


class KnowledgeGuardWiringTest(unittest.TestCase):
    def test_patrol_trusted_as_capacity_reader(self):
        guards = GuardStack(repo_root=REPO_ROOT)
        providers = load_providers("hearth.toolsurface.patrol,hearth.toolsurface.knowledge")
        wire_knowledge_guards(guards, providers)

        # This is patrol's real default call shape; it must not be rejected.
        guards.check("patrol", {"capacity_path": "knowledge/capacity.json", "refresh": True})

    def test_unrelated_tool_still_refused_on_knowledge_path(self):
        guards = GuardStack(repo_root=REPO_ROOT)
        providers = load_providers("hearth.toolsurface.patrol,hearth.toolsurface.knowledge")
        wire_knowledge_guards(guards, providers)

        with self.assertRaises(GuardRejection):
            guards.check("fs_write", {"path": "knowledge/capacity.json", "content": "{}"})


if __name__ == "__main__":
    unittest.main()

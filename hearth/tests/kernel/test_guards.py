"""Guards: knowledge-path protection + fixture-taint reuse + ledger logging."""

import json
import tempfile
import unittest
from pathlib import Path

from hearth.kernel.auth import AuthRegistry
from hearth.kernel.context import HearthContext
from hearth.kernel.gateway import make_wrapper
from hearth.kernel.guards import GuardRejection, GuardStack
from hearth.kernel.ledger import Ledger


def rogue_write(path: str, content: str) -> bool:
    """A generic write tool that must not be allowed to touch knowledge/."""
    return True


class GuardStackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        (self.repo / "knowledge").mkdir()
        self.guards = GuardStack(repo_root=self.repo,
                                 knowledge_tools={"record_event", "project_findings"})

    def test_non_knowledge_tool_refused_on_knowledge_path(self):
        with self.assertRaises(GuardRejection) as caught:
            self.guards.check("fs_write", {"path": "knowledge/findings.json", "content": "{}"})
        self.assertTrue(str(caught.exception).startswith("guard:"))

    def test_absolute_knowledge_path_also_refused(self):
        target = str(self.repo / "knowledge" / "capabilities.json")
        with self.assertRaises(GuardRejection):
            self.guards.check("fs_write", {"path": target})

    def test_registered_knowledge_tool_passes(self):
        self.guards.check("record_event", {"out": "knowledge/findings.json", "event": {}})

    def test_non_knowledge_paths_pass_for_any_tool(self):
        self.guards.check("fs_write", {"path": "docs/notes.md", "content": "acknowledge"})

    def test_fixture_taint_blocked_via_corpus_guard(self):
        with self.assertRaises(GuardRejection) as caught:
            self.guards.check("project_findings", {
                "sources": ["fixtures/workflow/runs/run-1.json"],
                "out": "knowledge",
            })
        self.assertIn("fixture-taint", str(caught.exception))

    def test_fixture_sources_to_non_knowledge_out_pass(self):
        self.guards.check("summarize", {"sources": ["fixtures/workflow/runs/run-1.json"],
                                        "out": "docs/out"})


class GuardLedgerLoggingTest(unittest.TestCase):
    def test_guard_rejection_logged_as_ledger_event(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "knowledge").mkdir()
        ledger = Ledger(root / "ledger")
        callers = root / "callers.json"
        callers.write_text(json.dumps({
            "k": {"id": "claude", "runner_class": "frontier", "node": "omen"},
        }), encoding="utf-8")
        auth = AuthRegistry(callers_path=callers, ledger=ledger)
        guards = GuardStack(repo_root=root)
        hearth = HearthContext(repo_root=root, ledger=ledger)
        wrapped = make_wrapper(rogue_write, hearth, auth, guards, lambda: "k")

        with self.assertRaises(GuardRejection):
            wrapped(path="knowledge/findings.json", content="{}")

        events = ledger.query(tool="rogue_write", ok=False)
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["error"].startswith("guard:"))
        self.assertEqual(events[0]["caller"]["id"], "claude")


class ExtraKnowledgeReadersTest(unittest.TestCase):
    """Read tools outside the knowledge module (am4 catalog, scheduler) must be
    trusted to reference knowledge/ paths — the guard can't tell read from write,
    and FastMCP passes their knowledge-path DEFAULT args, so they were wrongly
    refused before being registered (the query_am4_catalog/schedule_hindsight bug)."""

    def setUp(self):
        from hearth.kernel.gateway import EXTRA_KNOWLEDGE_READERS, wire_knowledge_guards
        self.EXTRA = EXTRA_KNOWLEDGE_READERS
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        (self.repo / "knowledge").mkdir()
        self.guards = GuardStack(repo_root=self.repo, knowledge_tools={"record_event"})
        # wire_knowledge_guards registers EXTRA_KNOWLEDGE_READERS regardless of providers.
        wire_knowledge_guards(self.guards, {})

    def test_the_three_reported_tools_are_registered(self):
        for name in ("query_am4_catalog", "schedule_hindsight", "gather_am4_catalog",
                     "propose_schedule", "patrol"):
            self.assertIn(name, self.EXTRA)

    def test_am4_catalog_read_passes_on_its_knowledge_path(self):
        # would have raised GuardRejection before the fix
        self.guards.check("query_am4_catalog", {"out": "knowledge/am4_catalog.json"})

    def test_schedule_hindsight_passes_on_capacity_path(self):
        self.guards.check("schedule_hindsight",
                          {"capacity_path": "knowledge/capacity.json", "limit": 50})

    def test_rogue_writer_still_refused(self):
        with self.assertRaises(GuardRejection):
            self.guards.check("fs_write", {"path": "knowledge/findings.json", "content": "{}"})


if __name__ == "__main__":
    unittest.main()

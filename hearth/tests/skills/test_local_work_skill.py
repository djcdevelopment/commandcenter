"""The local-work skill mirrors only the mounted, capability-classified surface."""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from unittest import TestCase

import yaml

from hearth.kernel.capabilities import TOOL_CAPABILITY
from hearth.toolsurface import local_work


REPO = Path(__file__).resolve().parents[3]
CLAUDE = REPO / ".claude" / "skills" / "local-work" / "SKILL.md"
CODEX = REPO / "docs" / "agents" / "codex-skills" / "local-work" / "SKILL.md"
CODEX_YAML = CODEX.parent / "agents" / "openai.yaml"
LAUNCHER = REPO / "hearth" / "etc" / "start-hearth-gateway.cmd"
TOOLS = {
    "submit_local_work", "watch_local_work", "get_local_work",
    "get_local_work_artifact", "record_local_work_verdict",
}


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    return yaml.safe_load(text[4:end + 1])


class LocalWorkSkillTests(TestCase):
    def test_tracked_mirrors_and_codex_descriptor_exist(self) -> None:
        self.assertTrue(CLAUDE.is_file())
        self.assertTrue(CODEX.is_file())
        self.assertTrue(CODEX_YAML.is_file())
        for path in (CLAUDE, CODEX):
            frontmatter = _frontmatter(path)
            self.assertEqual(frontmatter["name"], "local-work")
            self.assertTrue(frontmatter["description"].strip())

    def test_every_driven_tool_is_mounted_classified_and_real(self) -> None:
        exported = {tool.__name__: tool for tool in local_work.get_tools()}
        self.assertEqual(set(exported), TOOLS)
        for name in TOOLS:
            self.assertIn(name, TOOL_CAPABILITY)
            self.assertTrue(inspect.signature(exported[name]).parameters)
        launched = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("hearth.toolsurface.local_work", launched)

    def test_skills_name_the_same_lifecycle_and_never_authorize_apply(self) -> None:
        claude = CLAUDE.read_text(encoding="utf-8")
        codex = CODEX.read_text(encoding="utf-8")
        for name in TOOLS:
            self.assertIn(name, claude)
            self.assertIn(f"mcp__hearth__{name}", codex)
        for text in (claude, codex):
            self.assertRegex(text, re.compile(r"never apply", re.I))
            self.assertIn("awaiting_review", text)
            self.assertIn("accepted", text)
            self.assertIn("rejected", text)
            self.assertIn("superseded", text)

    def test_codex_descriptor_uses_the_loopback_door(self) -> None:
        document = yaml.safe_load(CODEX_YAML.read_text(encoding="utf-8"))
        tool = document["dependencies"]["tools"][0]
        self.assertEqual(tool["url"], "http://127.0.0.1:8710/mcp")

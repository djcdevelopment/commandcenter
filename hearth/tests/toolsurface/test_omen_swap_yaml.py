"""Line-level invariants on fleet/arcserve/llama-swap/omen.yaml (P6, ADR-0045).

No YAML parser in the stdlib, so these assert on the text: the shape llama-swap v251
documents, the ADR-0042 sibling-entry rule, the ADR-0031 arithmetic the rung declares,
and the secrets rule (no --api-key literal anywhere).
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
YAML_PATH = REPO / "fleet" / "arcserve" / "llama-swap" / "omen.yaml"
SERVE_ARC = REPO / "fleet" / "arcserve" / "serve-arc.cmd"

SIDE_MODELS = ("phi4", "qwen14b", "gptoss20b", "mistral24b")


def _text() -> str:
    return YAML_PATH.read_text(encoding="utf-8")


def _entry(text: str, model_id: str) -> str:
    """The block for one model entry (from its key to the next top-level model key)."""
    match = re.search(r'^  "%s":\n(.*?)(?=^  "|^hooks:|^routing:)' % re.escape(model_id),
                      text, re.S | re.M)
    assert match, "entry %r not found" % model_id
    return match.group(1)


class OmenSwapYamlTests(unittest.TestCase):
    def test_file_exists_and_declares_the_documented_shape(self) -> None:
        text = _text()
        self.assertIn("healthCheckTimeout: 300", text)
        self.assertIn("logLevel: debug", text)
        self.assertIn("routing:", text)
        self.assertIn("use: group", text)
        self.assertIn('hooks:\n  on_startup:\n    preload:\n      - "qwen3-30b-a3b"', text)

    def test_production_entry_keeps_port_8082_behind_a_proxy_and_has_no_device_env(self) -> None:
        entry = _entry(_text(), "qwen3-30b-a3b")
        self.assertIn("--host 127.0.0.1 --port 8082", entry)
        self.assertIn("proxy: http://127.0.0.1:8082", entry)
        self.assertNotIn("${PORT}", entry)
        self.assertNotIn("GGML_VK_VISIBLE_DEVICES", entry)
        self.assertIn("-lv 5", entry)
        self.assertIn("ttl: 0", entry)

    def test_production_command_matches_serve_arc_cmd_flags(self) -> None:
        """The campaign-proven flags travel verbatim; only the log/verbosity flags are added."""
        entry = _entry(_text(), "qwen3-30b-a3b")
        serve = SERVE_ARC.read_text(encoding="utf-8", errors="replace")
        for flag in ("-ngl 99 -sm layer -ts 1,1", "-fa on", "--no-mmap -dio -fit off",
                     "-c 131072 -np 2 -ub 1024", "--slots --jinja --metrics",
                     "Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf", "--alias qwen3-30b-a3b"):
            self.assertIn(flag, entry, flag)
            self.assertIn(flag, serve, "serve-arc.cmd no longer carries %r" % flag)

    def test_every_side_model_is_declared_twice_with_index_1_and_2(self) -> None:
        text = _text()
        for model in SIDE_MODELS:
            for index in ("1", "2"):
                entry = _entry(text, "%s-vk%s" % (model, index))
                self.assertIn('"GGML_VK_VISIBLE_DEVICES=%s"' % index, entry)
                self.assertIn("--port ${PORT}", entry)
                self.assertIn("--host 127.0.0.1", entry)
                self.assertIn("-lv 5", entry)
                self.assertIn("--slot-save-path", entry)
                self.assertIn("--alias %s-vk%s" % (model, index), entry)

    def test_dual_entry_has_no_device_env(self) -> None:
        entry = _entry(_text(), "qwen38-27b-dual")
        self.assertNotIn("GGML_VK_VISIBLE_DEVICES", entry)
        self.assertIn("-sm layer -ts 1,1", entry)

    def test_device_env_values_are_only_1_or_2(self) -> None:
        values = re.findall(r"GGML_VK_VISIBLE_DEVICES=(\S+?)\"", _text())
        self.assertTrue(values)
        self.assertEqual(set(values), {"1", "2"})

    def test_no_secret_literal_anywhere(self) -> None:
        # Comments may DESCRIBE the rule; the config itself must not carry a flag or a token.
        text = "\n".join(line for line in _text().splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("api-key", text)
        self.assertNotIn("Bearer ", text)
        self.assertNotRegex(text, r"OMEN_ARC_TOKEN\s*=")

    def test_groups_production_persistent_and_side_groups_non_exclusive(self) -> None:
        text = _text()
        prod = re.search(r'"production":\n(.*?)(?=\n        ")', text, re.S)
        self.assertIsNotNone(prod)
        self.assertIn("persistent: true", prod.group(1))
        self.assertIn("swap: false", prod.group(1))
        self.assertIn("exclusive: false", prod.group(1))
        # No side group may unload other groups (that would unload production).
        for model in SIDE_MODELS:
            block = re.search(r'"%s":\n(.*?)(?=\n        "|\Z)' % model, text, re.S)
            self.assertIsNotNone(block, model)
            self.assertIn("exclusive: false", block.group(1), model)
            self.assertIn("swap: true", block.group(1), model)
            self.assertIn('- "%s-vk1"' % model, block.group(1))
            self.assertIn('- "%s-vk2"' % model, block.group(1))

    def test_every_model_in_a_group_is_a_declared_model(self) -> None:
        text = _text()
        declared = set(re.findall(r'^  "([^"]+)":', text, re.M))
        members = set(re.findall(r'^            - "([^"]+)"', text, re.M))
        self.assertTrue(members)
        self.assertTrue(members <= declared, members - declared)


if __name__ == "__main__":
    unittest.main()

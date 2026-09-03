"""KV manifest + hydration (P3): names carry identity; cross-model restore is refused before HTTP."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hearth.rotation.kv import (CrossModelRestore, KvManifest, kv_filename, prompt_hash,
                                restore_slot, save_slot)


class _Client:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def slot_action(self, model_id, slot, action, filename, timeout_s=120.0):
        self.calls.append((model_id, slot, action, filename))
        default = {"ok": True, "id_slot": slot, "filename": filename, "n_saved": 2900, "n_written": 2_680_000_000}
        return self.responses.get(action, default)


class KvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manifest = KvManifest(Path(self.tmp.name) / "kv-manifest.json")

    def test_hash_and_name_are_deterministic_and_safe(self) -> None:
        self.assertEqual(prompt_hash("abc"), prompt_hash("abc"))
        self.assertEqual(len(prompt_hash("abc")), 16)
        self.assertEqual(kv_filename("phi4-vk1", 0, "deadbeef00000000"), "phi4-vk1.0.deadbeef00000000.bin")
        self.assertEqual(kv_filename("author/model:tag", 1, "x"), "author_model_tag.1.x.bin")

    def test_save_records_an_entry_and_round_trips_through_the_file(self) -> None:
        client = _Client()
        entry = save_slot(client, "phi4-vk1", 0, "hello world", self.manifest)
        self.assertEqual(client.calls, [("phi4-vk1", 0, "save", entry.filename)])
        self.assertEqual(entry.n_tokens, 2900)
        reloaded = KvManifest(self.manifest.path)
        self.assertEqual(reloaded.lookup("phi4-vk1", prompt_hash("hello world")), entry)

    def test_restore_uses_the_recorded_filename(self) -> None:
        client = _Client({"restore": {"ok": True, "n_restored": 2900, "n_read": 2_680_000_000,
                                      "timings": {"restore_ms": 1190}}})
        save_slot(client, "phi4-vk1", 0, "hello world", self.manifest)
        out = restore_slot(client, "phi4-vk1", 0, "hello world", self.manifest)
        self.assertTrue(out["ok"])
        self.assertEqual(out["n_restored"], 2900)
        self.assertEqual(client.calls[-1][2:], ("restore", out["entry"]["filename"]))

    def test_cross_model_restore_is_refused_before_any_http(self) -> None:
        client = _Client()
        save_slot(client, "phi4-vk1", 0, "hello world", self.manifest)
        calls_before = len(client.calls)
        with self.assertRaises(CrossModelRestore):
            restore_slot(client, "qwen14b-vk1", 0, "hello world", self.manifest)
        self.assertEqual(len(client.calls), calls_before)

    def test_unknown_prompt_is_a_lookup_error_not_a_request(self) -> None:
        client = _Client()
        with self.assertRaises(LookupError):
            restore_slot(client, "phi4-vk1", 0, "never saved", self.manifest)
        self.assertEqual(client.calls, [])

    def test_save_failure_leaves_the_manifest_untouched(self) -> None:
        client = _Client({"save": {"ok": False, "error": "HTTP 500"}})
        with self.assertRaises(RuntimeError):
            save_slot(client, "phi4-vk1", 0, "hello world", self.manifest)
        self.assertEqual(self.manifest.entries, {})
        self.assertFalse(self.manifest.path.exists())


if __name__ == "__main__":
    unittest.main()

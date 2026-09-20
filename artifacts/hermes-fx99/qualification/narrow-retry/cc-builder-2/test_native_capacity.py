import datetime
import unittest
from native_capacity import normalize_am4_native

NOW = datetime.datetime(2026, 9, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)
OBS = "2026-09-20T12:00:00+00:00"


def row(**kw):
    base = {"alias": "am4-dense-27b", "ready": True, "context_length": 131072,
            "parallel_slots": 1, "physical_resource": "am4:127.0.0.1:18090",
            "model": "models/Qwen3.8-27B-Q4_K_M.gguf", "status": 200}
    base.update(kw)
    return base


def payload(rows):
    return {"all_ready": True, "aliases": rows}


class T(unittest.TestCase):
    def _run(self, rows, obs=OBS):
        return normalize_am4_native(payload(rows), obs, now=NOW)

    def test_valid_ready(self):
        r = self._run([row()])
        self.assertTrue(r["ready"])
        self.assertEqual(r["parallel_slots"], 1)
        self.assertIsNone(r["gpu_placed"])
        self.assertEqual(r["context_length"], 131072)

    def test_ready_false(self):
        r = self._run([row(ready=False)])
        self.assertFalse(r["ready"])
        self.assertEqual(r["parallel_slots"], 0)

    def test_absent_row(self):
        r = self._run([row(alias="other")])
        self.assertFalse(r["ready"])

    def test_malformed_payload(self):
        r = normalize_am4_native("nope", OBS, now=NOW)
        self.assertFalse(r["ready"])

    def test_stale_timestamp(self):
        r = self._run([row()], obs="2026-09-20T11:59:00+00:00")
        self.assertFalse(r["ready"])

    def test_future_timestamp(self):
        r = self._run([row()], obs="2026-09-20T12:00:31+00:00")
        self.assertFalse(r["ready"])

    def test_wrong_model(self):
        r = self._run([row(model="models/other.gguf")])
        self.assertFalse(r["ready"])

    def test_wrong_context_bool(self):
        r = self._run([row(context_length=True)])
        self.assertFalse(r["ready"])

    def test_wrong_slot_count(self):
        r = self._run([row(parallel_slots=2)])
        self.assertFalse(r["ready"])

    def test_two_aliases_one_resource(self):
        r = self._run([row(), row(alias="am4-dense-27b")])
        self.assertFalse(r["ready"])


if __name__ == "__main__":
    unittest.main()

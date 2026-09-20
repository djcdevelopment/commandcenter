import datetime
import unittest
from native_capacity import normalize_am4_native

NOW = datetime.datetime(2026, 9, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)
OBS = "2026-09-20T12:00:00+00:00"


def payload(**over):
    row = {"alias": "am4-dense-27b", "ready": True, "context_length": 131072,
           "parallel_slots": 1, "physical_resource": "am4:127.0.0.1:18090",
           "model": "models/Qwen3.8-27B-Q4_K_M.gguf", "status": 200}
    row.update(over)
    return {"all_ready": True, "aliases": [row]}


class T(unittest.TestCase):
    def ok(self, p):
        return normalize_am4_native(p, OBS, NOW)

    def test_valid(self):
        r = self.ok(payload())
        self.assertTrue(r["ready"])
        self.assertEqual(r["parallel_slots"], 1)
        self.assertIsNone(r["gpu_placed"])
        self.assertEqual(r["context_length"], 131072)

    def test_not_ready(self):
        self.assertFalse(self.ok(payload(ready=False))["ready"])

    def test_absent(self):
        self.assertFalse(self.ok({"all_ready": True, "aliases": []})["ready"])

    def test_malformed(self):
        self.assertFalse(self.ok(None)["ready"])
        self.assertFalse(self.ok("x")["ready"])

    def test_stale(self):
        self.assertFalse(normalize_am4_native(payload(), "2026-09-20T11:59:00+00:00", NOW)["ready"])

    def test_future(self):
        self.assertFalse(normalize_am4_native(payload(), "2026-09-20T12:00:31+00:00", NOW)["ready"])

    def test_wrong_model(self):
        self.assertFalse(self.ok(payload(model="models/other.gguf"))["ready"])

    def test_bool_ctx(self):
        self.assertFalse(self.ok(payload(context_length=True))["ready"])

    def test_wrong_slots(self):
        self.assertFalse(self.ok(payload(parallel_slots=2))["ready"])

    def test_two_aliases_one_resource(self):
        p = payload()
        p["aliases"].append(dict(p["aliases"][0], alias="am4-dense-27b-b"))
        r = self.ok(p)
        self.assertTrue(r["ready"])
        self.assertEqual(r["parallel_slots"], 1)

    def test_duplicate_conflict(self):
        p = payload()
        p["aliases"].append(dict(p["aliases"][0], ready=False))
        self.assertFalse(self.ok(p)["ready"])


if __name__ == "__main__":
    unittest.main()

"""LlamaSwapClient (P3): exact URLs, readiness = health 200 AND a completion with timings."""
from __future__ import annotations

import json
import unittest

from hearth.rotation.swapclient import LlamaSwapClient, LoadOutcome


class _Fake:
    """Scripted fetch: maps (method, path-suffix) -> (status, body). Records every call."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, method, body, timeout_s, headers):
        path = url.split("127.0.0.1:8081", 1)[1] if "127.0.0.1:8081" in url else url
        self.calls.append((method, path, body, dict(headers)))
        for key, value in self.routes.items():
            if key[0] == method and path.startswith(key[1]):
                handler = value
                if callable(handler):
                    handler = handler(self.calls)
                status, payload = handler
                text = payload if isinstance(payload, str) else json.dumps(payload)
                err = None if status == 200 else f"HTTP {status}"
                return status, text, err
        return 0, "", "URLError: refused"


class ClientTests(unittest.TestCase):
    def _client(self, fake, **kw):
        ticks = iter(range(0, 10_000))
        return LlamaSwapClient(fetch=fake, token_env=None, clock=lambda: float(next(ticks)),
                               sleep=lambda s: None, **kw)

    def test_running_parses_the_documented_shape(self) -> None:
        fake = _Fake({("GET", "/running"): (200, {"running": [{"model": "qwen3-30b-a3b", "state": "ready"},
                                                              {"model": "phi4-vk1", "state": "starting"}]})})
        client = self._client(fake)
        models = client.running()
        self.assertEqual([m.model_id for m in models], ["qwen3-30b-a3b", "phi4-vk1"])
        self.assertTrue(models[0].ready)
        self.assertFalse(models[1].ready)
        self.assertTrue(client.is_resident("qwen3-30b-a3b"))
        self.assertFalse(client.is_resident("phi4-vk1"))

    def test_unload_uses_the_path_form_never_the_bare_endpoint(self) -> None:
        fake = _Fake({("POST", "/api/models/unload/phi4-vk1"): (200, "")})
        client = self._client(fake)
        self.assertTrue(client.unload("phi4-vk1"))
        self.assertEqual(fake.calls[-1][1], "/api/models/unload/phi4-vk1")
        with self.assertRaises(ValueError):
            client.unload("")

    def test_unload_all_is_a_separate_deliberate_call(self) -> None:
        fake = _Fake({("POST", "/api/models/unload"): (200, "")})
        client = self._client(fake)
        self.assertTrue(client.unload_all())
        self.assertEqual(fake.calls[-1][1], "/api/models/unload")

    def test_slot_action_posts_the_filename_to_the_upstream(self) -> None:
        fake = _Fake({("POST", "/upstream/phi4-vk1/slots/0?action=save"):
                      (200, {"id_slot": 0, "filename": "phi4-vk1.0.abc.bin", "n_saved": 3000})})
        client = self._client(fake)
        out = client.slot_action("phi4-vk1", 0, "save", "phi4-vk1.0.abc.bin")
        self.assertTrue(out["ok"])
        self.assertEqual(out["n_saved"], 3000)
        self.assertEqual(json.loads(fake.calls[-1][2]), {"filename": "phi4-vk1.0.abc.bin"})
        with self.assertRaises(ValueError):
            client.slot_action("phi4-vk1", 0, "delete", "x")

    def test_wait_ready_is_not_fooled_by_health_200_without_timings(self) -> None:
        """A 503-then-200 upstream that serves completions without a timings block is NOT ready."""
        fake = _Fake({("GET", "/upstream/phi4-vk1/health"): lambda calls: (503, "") if len(calls) < 3 else (200, "OK"),
                      ("POST", "/upstream/phi4-vk1/completion"): (200, {"content": "ok"})})
        client = self._client(fake)
        outcome = client.wait_ready("phi4-vk1", deadline_s=10, poll_s=1)
        self.assertIsInstance(outcome, LoadOutcome)
        self.assertFalse(outcome.ready)
        self.assertEqual(outcome.first_status, 503)
        self.assertIn("timings", outcome.error)

    def test_wait_ready_succeeds_on_timings(self) -> None:
        fake = _Fake({("GET", "/upstream/phi4-vk1/health"): (200, "OK"),
                      ("POST", "/upstream/phi4-vk1/completion"):
                      (200, {"content": "ok", "timings": {"prompt_ms": 12.0, "predicted_n": 1}})})
        client = self._client(fake)
        outcome = client.wait_ready("phi4-vk1", deadline_s=10, poll_s=1)
        self.assertTrue(outcome.ready)
        self.assertEqual(outcome.canary_timings["predicted_n"], 1)
        self.assertEqual(outcome.attempts, 1)

    def test_wait_ready_fails_fast_on_an_unknown_model(self) -> None:
        fake = _Fake({("GET", "/upstream/phi4-vk0/health"): (404, "model not found")})
        client = self._client(fake)
        outcome = client.wait_ready("phi4-vk0", deadline_s=30, poll_s=1)
        self.assertFalse(outcome.ready)
        self.assertEqual(outcome.first_status, 404)
        self.assertEqual(outcome.attempts, 1)
        self.assertIn("404", outcome.error)

    def test_wait_ready_times_out_when_upstream_never_answers(self) -> None:
        fake = _Fake({})
        client = self._client(fake)
        outcome = client.wait_ready("phi4-vk1", deadline_s=5, poll_s=1)
        self.assertFalse(outcome.ready)
        self.assertEqual(outcome.first_status, 0)

    def test_bearer_rides_completions_but_never_health(self) -> None:
        import os
        from unittest.mock import patch
        fake = _Fake({("GET", "/health"): (200, "OK"),
                      ("POST", "/upstream/m/completion"): (200, {"timings": {"predicted_n": 1}})})
        with patch.dict(os.environ, {"T_ENV": "secret-token"}):
            client = LlamaSwapClient(fetch=fake, token_env="T_ENV", sleep=lambda s: None)
            self.assertTrue(client.health())
            client.completion("m", "ok")
        self.assertNotIn("Authorization", fake.calls[0][3])
        self.assertEqual(fake.calls[1][3].get("Authorization"), "Bearer secret-token")


if __name__ == "__main__":
    unittest.main()

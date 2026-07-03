from __future__ import annotations

import json
import os
import urllib.error
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface.inference import local_generate


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._data = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


OLLAMA_REPLY = {
    "model": "qwen3-coder:30b",
    "response": "the answer",
    "prompt_eval_count": 12,
    "eval_count": 34,
    "total_duration": 2_500_000_000,  # 2.5s in nanoseconds
}


class LocalGenerateTests(TestCase):
    def test_success_maps_ollama_fields(self) -> None:
        with patch("urllib.request.urlopen", return_value=_FakeResponse(OLLAMA_REPLY)) as mocked:
            result = local_generate("what burns at the center of the camp?")

        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "the answer")
        self.assertEqual(result["model"], "qwen3-coder:30b")
        self.assertEqual(result["tokens_in"], 12)
        self.assertEqual(result["tokens_out"], 34)
        self.assertEqual(result["duration_ms"], 2500)

        sent = mocked.call_args[0][0]
        self.assertEqual(sent.full_url, "http://127.0.0.1:11434/api/generate")
        body = json.loads(sent.data.decode("utf-8"))
        self.assertFalse(body["stream"])
        self.assertEqual(body["options"]["num_predict"], 1024)
        self.assertNotIn("system", body)

    def test_system_prompt_and_max_tokens_forwarded(self) -> None:
        with patch("urllib.request.urlopen", return_value=_FakeResponse(OLLAMA_REPLY)) as mocked:
            local_generate("q", system="be brief", max_tokens=64)
        body = json.loads(mocked.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(body["system"], "be brief")
        self.assertEqual(body["options"]["num_predict"], 64)

    def test_connection_failure_returns_result_not_exception(self) -> None:
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("connection refused")):
            result = local_generate("anyone home?")
        self.assertFalse(result["ok"])
        self.assertIn("URLError", result["error"])
        self.assertNotIn("text", result)

    def test_hearth_ollama_env_overrides_default_endpoint(self) -> None:
        with patch.dict(os.environ, {"HEARTH_OLLAMA": "http://100.124.12.37:11434"}):
            with patch("urllib.request.urlopen", return_value=_FakeResponse(OLLAMA_REPLY)) as mocked:
                result = local_generate("hello omen")
        self.assertEqual(mocked.call_args[0][0].full_url,
                         "http://100.124.12.37:11434/api/generate")
        self.assertEqual(result["endpoint"], "http://100.124.12.37:11434")

    def test_explicit_endpoint_beats_env_override(self) -> None:
        with patch.dict(os.environ, {"HEARTH_OLLAMA": "http://100.124.12.37:11434"}):
            with patch("urllib.request.urlopen", return_value=_FakeResponse(OLLAMA_REPLY)) as mocked:
                local_generate("hello", endpoint="http://127.0.0.1:9999")
        self.assertEqual(mocked.call_args[0][0].full_url, "http://127.0.0.1:9999/api/generate")

    def test_bad_inputs_rejected(self) -> None:
        with self.assertRaises(ValueError):
            local_generate("   ")
        with self.assertRaises(ValueError):
            local_generate("ok", max_tokens=0)
        with self.assertRaises(ValueError):
            local_generate("ok", timeout_s=-1)
        with self.assertRaises(ValueError):
            local_generate("ok", model="")

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.error
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface.inference import local_generate

# P1 test intent predates occupancy (P2): every pre-existing test in this file
# exercises routing, not occupancy, so force "available" everywhere here to keep
# them hermetic (no live SSH to AM4) and behaviorally unchanged. P2's own skip
# semantics get a dedicated class below with per-test occupancy patches.
_ALWAYS_AVAILABLE = patch(
    "hearth.toolsurface.inference.check_occupancy",
    return_value={"occupancy": "available"},
)


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

    def test_default_path_reports_backend_provenance(self) -> None:
        with patch("urllib.request.urlopen", return_value=_FakeResponse(OLLAMA_REPLY)):
            result = local_generate("hello")
        self.assertEqual(result["backend"], "omen-ollama")
        self.assertEqual(result["routed_by"], "default")
        self.assertEqual(result["occupancy"], "available")


OPENAI_REPLY = {
    "model": "qwen3-30b",
    "choices": [{"message": {"role": "assistant", "content": "the banked answer"}}],
    "usage": {"prompt_tokens": 20, "completion_tokens": 40},
}

GEMINI_REPLY = {
    "candidates": [
        {"content": {"parts": [{"text": "cloud "}, {"text": "answer"}]}}
    ],
    "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 50},
}


class BankedFireRoutingTests(TestCase):
    """P1: task/backend routing to the OpenAI-shaped oxen backend."""

    def setUp(self) -> None:
        # These tests exercise P1 tag/name routing, not P2 occupancy — force
        # "available" so they never make a live SSH probe to AM4.
        self.enterContext(_ALWAYS_AVAILABLE)

    def test_task_research_routes_to_oxen_openai(self) -> None:
        with patch.dict(os.environ, {"AM4_OXEN_TOKEN": "sk-oxen"}):
            with patch("urllib.request.urlopen",
                       return_value=_FakeResponse(OPENAI_REPLY)) as mocked:
                result = local_generate("what is banked in the coals?", task="research")

        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "the banked answer")
        self.assertEqual(result["backend"], "am4-oxen")
        self.assertEqual(result["routed_by"], "tag:research")
        self.assertEqual(result["tokens_in"], 20)
        self.assertEqual(result["tokens_out"], 40)

        sent = mocked.call_args[0][0]
        self.assertEqual(sent.full_url, "http://192.168.12.233:8090/v1/chat/completions")
        self.assertEqual(sent.headers.get("Authorization"), "Bearer sk-oxen")
        body = json.loads(sent.data.decode("utf-8"))
        self.assertEqual(body["messages"][-1], {"role": "user",
                                                "content": "what is banked in the coals?"})
        self.assertEqual(body["model"], "oxen")  # backend's declared default model

    def test_system_prompt_becomes_openai_system_message(self) -> None:
        with patch.dict(os.environ, {"AM4_OXEN_TOKEN": "sk-oxen"}):
            with patch("urllib.request.urlopen",
                       return_value=_FakeResponse(OPENAI_REPLY)) as mocked:
                local_generate("q", backend="am4-oxen", system="be terse")
        body = json.loads(mocked.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(body["messages"][0], {"role": "system", "content": "be terse"})

    def test_openai_backend_without_token_is_clean_error(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch("urllib.request.urlopen") as mocked:
                result = local_generate("q", task="research")
        self.assertFalse(result["ok"])
        self.assertIn("AM4_OXEN_TOKEN", result["error"])
        self.assertEqual(result["backend"], "am4-oxen")
        mocked.assert_not_called()  # short-circuits before any 401-yielding POST

    def test_unknown_backend_name_returns_routing_error(self) -> None:
        result = local_generate("q", backend="ghost")
        self.assertFalse(result["ok"])
        self.assertIn("routing failed", result["error"])


class GeminiRoutingTests(TestCase):
    """GCP Gemini provider calls native generateContent with ADC-derived auth."""

    def setUp(self) -> None:
        self.enterContext(_ALWAYS_AVAILABLE)

    def test_pinned_gemini_backend_uses_generate_content(self) -> None:
        auth = subprocess.CompletedProcess(
            args=["gcloud"], returncode=0, stdout="ya29.token\n", stderr="")
        with patch.dict(os.environ, {
            "GOOGLE_CLOUD_PROJECT": "trial-project",
            "GOOGLE_CLOUD_LOCATION": "global",
        }, clear=True):
            with patch("hearth.toolsurface.inference._gcloud_executable",
                       return_value="gcloud"):
                with patch("subprocess.run", return_value=auth) as auth_mock:
                    with patch("urllib.request.urlopen",
                               return_value=_FakeResponse(GEMINI_REPLY)) as mocked:
                        result = local_generate("spend this carefully", backend="gcp-gemini",
                                                system="be exact", max_tokens=128)

        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "cloud answer")
        self.assertEqual(result["backend"], "gcp-gemini")
        self.assertEqual(result["model"], "gemini-3.5-flash")
        self.assertEqual(result["tokens_in"], 30)
        self.assertEqual(result["tokens_out"], 50)
        auth_mock.assert_called_once_with(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True,
            text=True,
            timeout=15,
        )

        sent = mocked.call_args[0][0]
        self.assertEqual(
            sent.full_url,
            "https://aiplatform.googleapis.com/v1/projects/trial-project/locations/global/"
            "publishers/google/models/gemini-3.5-flash:generateContent",
        )
        self.assertEqual(sent.headers.get("Authorization"), "Bearer ya29.token")
        body = json.loads(sent.data.decode("utf-8"))
        self.assertEqual(body["contents"][0]["parts"][0]["text"], "spend this carefully")
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "be exact")
        self.assertEqual(body["generationConfig"]["maxOutputTokens"], 128)

    def test_gemini_can_use_auth_env_without_gcloud(self) -> None:
        with patch.dict(os.environ, {
            "GOOGLE_CLOUD_PROJECT": "trial-project",
            "GOOGLE_OAUTH_ACCESS_TOKEN": "env-token",
        }, clear=True):
            with patch("subprocess.run") as auth_mock:
                with patch("urllib.request.urlopen",
                           return_value=_FakeResponse(GEMINI_REPLY)) as mocked:
                    result = local_generate("q", backend="gcp-gemini")
        self.assertTrue(result["ok"])
        auth_mock.assert_not_called()
        self.assertEqual(mocked.call_args[0][0].headers.get("Authorization"),
                         "Bearer env-token")

    def test_gemini_missing_project_is_clean_error(self) -> None:
        auth = subprocess.CompletedProcess(
            args=["gcloud"], returncode=0, stdout="ya29.token\n", stderr="")
        with patch.dict(os.environ, {}, clear=True):
            with patch("subprocess.run", return_value=auth):
                with patch("urllib.request.urlopen") as mocked:
                    result = local_generate("q", backend="gcp-gemini")
        self.assertFalse(result["ok"])
        self.assertIn("GOOGLE_CLOUD_PROJECT", result["error"])
        mocked.assert_not_called()

    def test_gemini_auth_failure_is_clean_error(self) -> None:
        auth = subprocess.CompletedProcess(
            args=["gcloud"], returncode=1, stdout="", stderr="not logged in")
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "trial-project"}, clear=True):
            with patch("subprocess.run", return_value=auth):
                with patch("urllib.request.urlopen") as mocked:
                    result = local_generate("q", backend="gcp-gemini")
        self.assertFalse(result["ok"])
        self.assertIn("gcloud auth print-access-token failed", result["error"])
        mocked.assert_not_called()


class OccupancyRoutingTests(TestCase):
    """P2: local_generate consults occupancy for tag-routed calls; a busy or
    unknown oxen backend is skipped in favor of omen-ollama, and every result
    carries the occupancy reading at decision time."""

    def test_busy_oxen_skipped_falls_back_to_ollama(self) -> None:
        with patch("hearth.toolsurface.inference.check_occupancy",
                   return_value={"occupancy": "busy"}):
            with patch("urllib.request.urlopen",
                       return_value=_FakeResponse(OLLAMA_REPLY)) as mocked:
                result = local_generate("what is banked in the coals?", task="research")
        self.assertTrue(result["ok"])
        self.assertEqual(result["backend"], "omen-ollama")
        self.assertEqual(result["routed_by"], "default")
        self.assertEqual(result["occupancy"], "available")
        self.assertEqual(mocked.call_args[0][0].full_url, "http://127.0.0.1:11434/api/generate")

    def test_unknown_oxen_skipped_falls_back_to_ollama(self) -> None:
        with patch("hearth.toolsurface.inference.check_occupancy",
                   return_value={"occupancy": "unknown", "detail": "ssh timed out"}):
            with patch("urllib.request.urlopen",
                       return_value=_FakeResponse(OLLAMA_REPLY)):
                result = local_generate("q", task="research")
        self.assertEqual(result["backend"], "omen-ollama")

    def test_available_oxen_routes_and_reports_occupancy(self) -> None:
        with patch.dict(os.environ, {"AM4_OXEN_TOKEN": "sk-oxen"}):
            with patch("hearth.toolsurface.inference.check_occupancy",
                       return_value={"occupancy": "available"}):
                with patch("urllib.request.urlopen",
                           return_value=_FakeResponse(OPENAI_REPLY)):
                    result = local_generate("q", task="research")
        self.assertEqual(result["backend"], "am4-oxen")
        self.assertEqual(result["occupancy"], "available")

    def test_pinned_backend_busy_still_dispatches_there(self) -> None:
        """A name-pinned backend is never occupancy-skipped, even busy."""
        with patch.dict(os.environ, {"AM4_OXEN_TOKEN": "sk-oxen"}):
            with patch("hearth.toolsurface.inference.check_occupancy",
                       return_value={"occupancy": "busy"}):
                with patch("urllib.request.urlopen",
                           return_value=_FakeResponse(OPENAI_REPLY)) as mocked:
                    result = local_generate("q", backend="am4-oxen")
        self.assertEqual(result["backend"], "am4-oxen")
        self.assertEqual(result["occupancy"], "busy")
        self.assertEqual(mocked.call_args[0][0].full_url,
                         "http://192.168.12.233:8090/v1/chat/completions")

    def test_pinned_endpoint_bypasses_occupancy_entirely(self) -> None:
        """An endpoint pin never even calls check_occupancy (path 1 short-circuits)."""
        with patch("hearth.toolsurface.inference.check_occupancy") as occ_mock:
            with patch("urllib.request.urlopen", return_value=_FakeResponse(OLLAMA_REPLY)):
                result = local_generate("q", endpoint="http://127.0.0.1:9999")
        occ_mock.assert_not_called()
        self.assertEqual(result["occupancy"], "available")


class FilesPackingTests(TestCase):
    """Repo-aware intake: files= packs scope-guarded content door-side, and any
    bad path raises BEFORE dispatch (no POST, no auth, no occupancy probe)."""

    def setUp(self) -> None:
        self.enterContext(_ALWAYS_AVAILABLE)
        self.temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"HEARTH_SCOPE": self.temp_dir}, clear=True))

    def test_files_content_packed_and_reported(self) -> None:
        notes_path = Path(self.temp_dir) / "notes.txt"
        notes_path.write_text("EMBER-SENTINEL-77", encoding="utf-8")

        with patch("urllib.request.urlopen", return_value=_FakeResponse(OLLAMA_REPLY)) as mocked:
            result = local_generate("what is the sentinel?", files=["notes.txt"])

        self.assertTrue(result.get("ok"), result.get("error"))
        payload = json.loads(mocked.call_args[0][0].data.decode("utf-8"))
        prompt = payload["prompt"]
        self.assertIn("EMBER-SENTINEL-77", prompt)
        self.assertIn('<file path="notes.txt">', prompt)
        self.assertIn("what is the sentinel?", prompt)

        self.assertEqual(result.get("files_packed"), [{"path": "notes.txt", "bytes": 17}])
        self.assertEqual(result.get("files_bytes"), 17)

    def test_files_absolute_from_secondary_root_packed(self) -> None:
        """Multi-root scope: an absolute path under a secondary HEARTH_SCOPE root
        packs fine and is labeled by absolute path (not repo-relative)."""
        other_dir = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        other = other_dir / "other.txt"
        other.write_text("CROSS-REPO-99", encoding="utf-8")
        scope = os.pathsep.join([self.temp_dir, str(other_dir)])
        self.enterContext(patch.dict(os.environ, {"HEARTH_SCOPE": scope}))

        with patch("urllib.request.urlopen", return_value=_FakeResponse(OLLAMA_REPLY)) as mocked:
            result = local_generate("q", files=[str(other)])

        self.assertTrue(result.get("ok"), result.get("error"))
        payload = json.loads(mocked.call_args[0][0].data.decode("utf-8"))
        self.assertIn("CROSS-REPO-99", payload["prompt"])
        self.assertIn(f'<file path="{other.as_posix()}">', payload["prompt"])
        self.assertEqual(result.get("files_packed"),
                         [{"path": other.as_posix(), "bytes": 13}])

    def test_files_escape_rejected(self) -> None:
        with patch("urllib.request.urlopen") as mocked:
            with self.assertRaises(ValueError):
                local_generate("q", files=["../evil.txt"])
            mocked.assert_not_called()

    def test_files_missing_rejected(self) -> None:
        with patch("urllib.request.urlopen") as mocked:
            with self.assertRaisesRegex(ValueError, "not an existing regular file"):
                local_generate("q", files=["ghost.txt"])
            mocked.assert_not_called()

    def test_files_over_cap_rejected(self) -> None:
        big_path = Path(self.temp_dir) / "big.txt"
        big_path.write_bytes(b"A" * 15)

        with patch("hearth.toolsurface.inference.FILES_TOTAL_CAP", 10):
            with patch("urllib.request.urlopen") as mocked:
                with self.assertRaisesRegex(ValueError, "exceeds"):
                    local_generate("q", files=["big.txt"])
                mocked.assert_not_called()

    def test_files_bad_type_rejected(self) -> None:
        with self.assertRaises(ValueError):
            local_generate("q", files="notes.txt")

    def test_no_files_leaves_result_shape_unchanged(self) -> None:
        with patch("urllib.request.urlopen", return_value=_FakeResponse(OLLAMA_REPLY)):
            result = local_generate("q")
        self.assertNotIn("files_packed", result)
        self.assertNotIn("files_bytes", result)


class GeminiDefaultBudgetTests(TestCase):
    """Per-rung output budgets: omitted max_tokens resolves from backend settings
    (the thinking-budget protection) on BOTH Gemini rungs."""

    def setUp(self) -> None:
        self.enterContext(_ALWAYS_AVAILABLE)
        self.enterContext(patch.dict(os.environ, {
            "GOOGLE_CLOUD_PROJECT": "trial-project",
            "GOOGLE_OAUTH_ACCESS_TOKEN": "env-token",
        }, clear=True))

    def test_flash_default_budget_is_8192(self) -> None:
        with patch("urllib.request.urlopen", return_value=_FakeResponse(GEMINI_REPLY)) as mocked:
            result = local_generate("q", backend="gcp-gemini")

        self.assertTrue(result.get("ok"), result.get("error"))
        payload = json.loads(mocked.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 8192)
        self.assertEqual(result["max_tokens"], 8192)

    def test_pro_default_budget_is_16384(self) -> None:
        with patch("urllib.request.urlopen", return_value=_FakeResponse(GEMINI_REPLY)) as mocked:
            result = local_generate("q", backend="gcp-gemini-pro")

        self.assertTrue(result.get("ok"), result.get("error"))
        payload = json.loads(mocked.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 16384)
        self.assertEqual(result["max_tokens"], 16384)
        self.assertEqual(result["model"], "gemini-3.1-pro-preview")

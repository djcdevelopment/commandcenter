from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.error
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface.backends import load_pool
from hearth.toolsurface.inference import DEFAULT_TIMEOUT_S, local_generate

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

    def test_task_research_routes_to_moe_openai(self) -> None:
        # Residency handover: research/big-context/second-opinion now land on
        # the resident gpt-oss-120b rung (am4-moe, :8082); the single-card
        # planner (am4-oxen) is pin-only.
        with patch.dict(os.environ, {"AM4_OXEN_TOKEN": "sk-oxen"}):
            with patch("urllib.request.urlopen",
                       return_value=_FakeResponse(OPENAI_REPLY)) as mocked:
                result = local_generate("what is banked in the coals?", task="research")

        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "the banked answer")
        self.assertEqual(result["backend"], "am4-moe")
        self.assertEqual(result["routed_by"], "tag:research")
        self.assertEqual(result["tokens_in"], 20)
        self.assertEqual(result["tokens_out"], 40)

        sent = mocked.call_args[0][0]
        self.assertEqual(sent.full_url, "http://192.168.12.233:8082/v1/chat/completions")
        self.assertEqual(sent.headers.get("Authorization"), "Bearer sk-oxen")
        body = json.loads(sent.data.decode("utf-8"))
        self.assertEqual(body["messages"][-1], {"role": "user",
                                                "content": "what is banked in the coals?"})
        self.assertEqual(body["model"], "gpt-oss-120b")  # backend's declared default model

    def test_system_prompt_becomes_openai_system_message(self) -> None:
        with patch.dict(os.environ, {"AM4_OXEN_TOKEN": "sk-oxen"}):
            with patch("urllib.request.urlopen",
                       return_value=_FakeResponse(OPENAI_REPLY)) as mocked:
                local_generate("q", backend="am4-oxen", system="be terse")
        body = json.loads(mocked.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(body["messages"][0], {"role": "system", "content": "be terse"})

    def test_openai_backend_without_token_is_clean_error(self) -> None:
        # Pinned so the clean error surfaces un-escalated: since A2, an
        # UNPINNED token failure climbs one rung instead (EscalationTests in
        # test_backends.py cover that path); a pin never escalates.
        with patch.dict(os.environ, {}, clear=True):
            with patch("urllib.request.urlopen") as mocked:
                result = local_generate("q", backend="am4-oxen")
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


_TRIAL_POOL_TOML = """
default = "omen-ollama"

[trial]
budget_tokens = 1000
reserve_tokens = 100

[[backend]]
name = "omen-ollama"
endpoint = "http://127.0.0.1:11434"
api = "ollama"
tags = ["default"]

[[backend]]
name = "gcp-gemini"
endpoint = "https://aiplatform.googleapis.com"
api = "gemini"
auth_env = "GOOGLE_OAUTH_ACCESS_TOKEN"
tags = ["cloud-overflow"]
settings = { cost_class = "trial" }
"""


class TrialSuppressionTests(TestCase):
    """A4: trial rungs leave opportunistic routing when the runway is spent."""

    def setUp(self) -> None:
        self.enterContext(_ALWAYS_AVAILABLE)
        self.temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"HEARTH_SCOPE": self.temp_dir}))

        pool_path = Path(self.temp_dir) / "backends.toml"
        pool_path.write_text(_TRIAL_POOL_TOML, encoding="utf-8")
        self.enterContext(patch.dict(os.environ, {"HEARTH_BACKENDS": str(pool_path)}))

        self.knowledge_dir = Path(self.temp_dir) / "knowledge"
        self.knowledge_dir.mkdir()
        self.offload = self.knowledge_dir / "offload.json"

    def test_suppression_healthy_burn_routes_gemini(self) -> None:
        self.offload.write_text(json.dumps(
            {"per_class": {"trial": {"tokens_in": 500, "tokens_out": 100}}}), encoding="utf-8")
        with patch.dict(os.environ, {"GOOGLE_OAUTH_ACCESS_TOKEN": "token",
                                     "GOOGLE_CLOUD_PROJECT": "p"}):
            with patch("urllib.request.urlopen", return_value=_FakeResponse(GEMINI_REPLY)):
                result = local_generate("q", task="cloud-overflow")
        self.assertEqual(result["backend"], "gcp-gemini")

    def test_suppression_high_burn_skips_gemini(self) -> None:
        self.offload.write_text(json.dumps(
            {"per_class": {"trial": {"tokens_in": 500, "tokens_out": 500}}}), encoding="utf-8")
        with patch.dict(os.environ, {"GOOGLE_OAUTH_ACCESS_TOKEN": "token",
                                     "GOOGLE_CLOUD_PROJECT": "p"}):
            with patch("urllib.request.urlopen", return_value=_FakeResponse(OLLAMA_REPLY)):
                result = local_generate("q", task="cloud-overflow")
        self.assertEqual(result["backend"], "omen-ollama")
        self.assertEqual(result["routed_by"], "default")

    def test_suppression_missing_offload_routes_gemini(self) -> None:
        with patch.dict(os.environ, {"GOOGLE_OAUTH_ACCESS_TOKEN": "token",
                                     "GOOGLE_CLOUD_PROJECT": "p"}):
            with patch("urllib.request.urlopen", return_value=_FakeResponse(GEMINI_REPLY)):
                result = local_generate("q", task="cloud-overflow")
        self.assertEqual(result["backend"], "gcp-gemini")

    def test_suppression_pinned_bypasses_suppression(self) -> None:
        self.offload.write_text(json.dumps(
            {"per_class": {"trial": {"tokens_in": 9999, "tokens_out": 9999}}}), encoding="utf-8")
        with patch.dict(os.environ, {"GOOGLE_OAUTH_ACCESS_TOKEN": "token",
                                     "GOOGLE_CLOUD_PROJECT": "p"}):
            with patch("urllib.request.urlopen", return_value=_FakeResponse(GEMINI_REPLY)):
                result = local_generate("q", backend="gcp-gemini")
        self.assertEqual(result["backend"], "gcp-gemini")


class QualityRoutingTests(TestCase):
    """A3: quality tiers — good prefers flash, best asks instead of dispatching."""

    def setUp(self) -> None:
        self.enterContext(_ALWAYS_AVAILABLE)

    def test_quality_best_returns_ask(self) -> None:
        with patch("urllib.request.urlopen") as mocked:
            result = local_generate("q", quality="best")
        mocked.assert_not_called()
        self.assertTrue(result["ask"])
        self.assertIsNone(result["backend"])
        self.assertEqual(result["routed_by"], "ask:quality-best")
        self.assertEqual(result["recommendation"]["backend"], "gcp-gemini-pro")

    def test_quality_good_routes_to_cloud_overflow(self) -> None:
        with patch.dict(os.environ, {"GOOGLE_OAUTH_ACCESS_TOKEN": "token",
                                     "GOOGLE_CLOUD_PROJECT": "p"}):
            with patch("urllib.request.urlopen", return_value=_FakeResponse(GEMINI_REPLY)):
                result = local_generate("q", quality="good")
        self.assertEqual(result["backend"], "gcp-gemini")
        self.assertEqual(result["routed_by"], "quality-good:tag:cloud-overflow")

    def test_quality_good_suppressed_routes_to_default(self) -> None:
        temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"HEARTH_SCOPE": temp_dir}))

        pool_path = Path(temp_dir) / "backends.toml"
        pool_path.write_text(_TRIAL_POOL_TOML.replace("reserve_tokens = 100",
                                                      "reserve_tokens = 0"), encoding="utf-8")
        self.enterContext(patch.dict(os.environ, {"HEARTH_BACKENDS": str(pool_path)}))

        knowledge_dir = Path(temp_dir) / "knowledge"
        knowledge_dir.mkdir()
        (knowledge_dir / "offload.json").write_text(json.dumps(
            {"per_class": {"trial": {"tokens_in": 1000}}}), encoding="utf-8")

        with patch("urllib.request.urlopen", return_value=_FakeResponse(OLLAMA_REPLY)):
            result = local_generate("q", quality="good")

        self.assertEqual(result["backend"], "omen-ollama")
        self.assertEqual(result["routed_by"], "quality-good:default")

    def test_invalid_quality_raises(self) -> None:
        with self.assertRaises(ValueError):
            local_generate("q", quality="terrible")


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
        self.assertEqual(result["occupancy"], "busy")
        self.assertEqual(mocked.call_args[0][0].full_url, "http://127.0.0.1:11434/api/generate")

    def test_unknown_oxen_skipped_falls_back_to_ollama(self) -> None:
        with patch("hearth.toolsurface.inference.check_occupancy",
                   return_value={"occupancy": "unknown", "detail": "ssh timed out"}):
            with patch("urllib.request.urlopen",
                       return_value=_FakeResponse(OLLAMA_REPLY)):
                result = local_generate("q", task="research")
        self.assertEqual(result["backend"], "omen-ollama")

    def test_available_moe_routes_and_reports_occupancy(self) -> None:
        with patch.dict(os.environ, {"AM4_OXEN_TOKEN": "sk-oxen"}):
            with patch("hearth.toolsurface.inference.check_occupancy",
                       return_value={"occupancy": "available"}):
                with patch("urllib.request.urlopen",
                           return_value=_FakeResponse(OPENAI_REPLY)):
                    result = local_generate("q", task="research")
        self.assertEqual(result["backend"], "am4-moe")
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


class RungTimeoutBudgetTests(TestCase):
    """O5 — per-rung WALL-CLOCK budgets resolve like max_tokens does
    (caller -> rung settings -> DEFAULT_TIMEOUT_S).

    Why this exists: a timeout is an ok:false, and an ok:false on a tag-routed
    call triggers A2 escalation. So a too-short budget does not merely fail —
    it silently reroutes slow-but-healthy sunk work onto a trial-credit rung,
    inverting the sunk-first ladder exactly where it matters most.
    """

    # Measured aggregate decode for the resident MoE, INCLUDING reasoning
    # tokens: ~21 tok/s p50 (knowledge/capacity.json, 2026-07-18), 26-29 tok/s
    # solo (2026-07-18-oxen-moe-gambit.md). The p50 is the honest planning
    # number for a full-budget answer, so the invariant below uses it.
    MOE_DECODE_TOK_S = 21

    def setUp(self) -> None:
        self.enterContext(_ALWAYS_AVAILABLE)
        self.enterContext(patch.dict(os.environ, {"AM4_OXEN_TOKEN": "sk-oxen"}, clear=True))

    def test_moe_timeout_comes_from_rung_settings(self) -> None:
        with patch("urllib.request.urlopen", return_value=_FakeResponse(OPENAI_REPLY)) as mocked:
            result = local_generate("q", backend="am4-moe")

        self.assertTrue(result.get("ok"), result.get("error"))
        self.assertEqual(mocked.call_args.kwargs["timeout"], 420)
        self.assertEqual(result["timeout_s"], 420)

    def test_rung_without_declared_timeout_falls_back_to_default(self) -> None:
        with patch("urllib.request.urlopen", return_value=_FakeResponse(OLLAMA_REPLY)) as mocked:
            result = local_generate("q", backend="omen-ollama")

        self.assertTrue(result.get("ok"), result.get("error"))
        self.assertEqual(mocked.call_args.kwargs["timeout"], DEFAULT_TIMEOUT_S)
        self.assertEqual(result["timeout_s"], DEFAULT_TIMEOUT_S)

    def test_explicit_caller_timeout_overrides_rung_setting(self) -> None:
        with patch("urllib.request.urlopen", return_value=_FakeResponse(OPENAI_REPLY)) as mocked:
            result = local_generate("q", backend="am4-moe", timeout_s=30)

        self.assertEqual(mocked.call_args.kwargs["timeout"], 30)
        self.assertEqual(result["timeout_s"], 30)

    def test_moe_timeout_budget_covers_its_own_output_budget(self) -> None:
        """The invariant the 120s default violated: a rung must be allowed
        enough wall-clock to actually emit the output budget it declares."""
        moe = load_pool().by_name("am4-moe")
        max_tokens = int(moe.settings["max_tokens"])
        timeout_s = int(moe.settings["timeout_s"])

        implied_s = max_tokens / self.MOE_DECODE_TOK_S
        self.assertGreaterEqual(
            timeout_s, implied_s,
            f"am4-moe declares max_tokens={max_tokens}, which needs ~{implied_s:.0f}s "
            f"at the measured {self.MOE_DECODE_TOK_S} tok/s, but only allows {timeout_s}s. "
            "Raise timeout_s, lower max_tokens, or re-measure the decode rate.",
        )

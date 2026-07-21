from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface.backends import (
    Backend,
    BackendConfigError,
    BackendRoutingRefusal,
    Pool,
    load_pool,
    select_backend,
)
from hearth.toolsurface.inference import local_generate

_POOL_TOML = textwrap.dedent("""
    default = "omen-ollama"

    [trial]
    budget_tokens = 5000000
    reserve_tokens = 500000

    [[backend]]
    name = "omen-ollama"
    endpoint = "http://127.0.0.1:11434"
    api = "ollama"
    models = ["qwen3-coder:30b"]
    tags = ["default", "code"]

    [[backend]]
    name = "am4-oxen"
    endpoint = "http://100.116.82.60:8090"
    api = "openai"
    auth_env = "AM4_OXEN_TOKEN"
    models = ["qwen3-30b"]
    tags = ["research", "big-context"]
    occupancy = { conductor_worker = "am4-worker-1" }

    [[backend]]
    name = "gcp-gemini"
    endpoint = "https://aiplatform.googleapis.com"
    api = "gemini"
    auth_env = "GOOGLE_OAUTH_ACCESS_TOKEN"
    models = ["gemini-3.5-flash"]
    tags = ["frontier", "cloud-overflow"]
    settings = { project_env = "GOOGLE_CLOUD_PROJECT", location_env = "GOOGLE_CLOUD_LOCATION", cost_class = "trial" }
""")


def _write_pool(tmp: Path, body: str = _POOL_TOML) -> Path:
    path = tmp / "backends.toml"
    path.write_text(body, encoding="utf-8")
    return path


class LoadPoolTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))

    def test_loads_declared_backends(self) -> None:
        pool = load_pool(_write_pool(self.tmp))
        self.assertEqual(pool.default, "omen-ollama")
        self.assertEqual(len(pool.backends), 3)
        oxen = pool.by_name("am4-oxen")
        self.assertEqual(oxen.api, "openai")
        self.assertEqual(oxen.auth_env, "AM4_OXEN_TOKEN")
        self.assertEqual(oxen.models, ("qwen3-30b",))
        self.assertEqual(oxen.occupancy, {"conductor_worker": "am4-worker-1"})
        gemini = pool.by_name("gcp-gemini")
        self.assertEqual(gemini.api, "gemini")
        self.assertEqual(gemini.auth_env, "GOOGLE_OAUTH_ACCESS_TOKEN")
        self.assertEqual(gemini.settings["project_env"], "GOOGLE_CLOUD_PROJECT")
        self.assertEqual(gemini.cost_class(), "trial")
        self.assertEqual(pool.trial["budget_tokens"], 5000000)

    def test_missing_file_falls_back_to_omen_ollama(self) -> None:
        pool = load_pool(self.tmp / "does-not-exist.toml")
        self.assertEqual(pool.default, "omen-ollama")
        self.assertEqual([b.name for b in pool.backends], ["omen-ollama"])

    def test_env_var_overrides_path_arg(self) -> None:
        declared = _write_pool(self.tmp)
        with patch.dict(os.environ, {"HEARTH_BACKENDS": str(declared)}):
            pool = load_pool()  # no arg -> env wins
        self.assertEqual(len(pool.backends), 3)

    def test_by_endpoint_is_trailing_slash_insensitive(self) -> None:
        pool = load_pool(_write_pool(self.tmp))
        self.assertEqual(pool.by_endpoint("http://127.0.0.1:11434/").name, "omen-ollama")
        self.assertIsNone(pool.by_endpoint("http://nope:1234"))

    def test_unknown_api_rejected(self) -> None:
        bad = _POOL_TOML.replace('api = "openai"', 'api = "grpc"')
        with self.assertRaises(BackendConfigError):
            load_pool(_write_pool(self.tmp, bad))

    def test_duplicate_names_rejected(self) -> None:
        dupe = _POOL_TOML.replace('name = "am4-oxen"', 'name = "omen-ollama"')
        with self.assertRaises(BackendConfigError):
            load_pool(_write_pool(self.tmp, dupe))

    def test_default_naming_missing_backend_rejected(self) -> None:
        bad = _POOL_TOML.replace('default = "omen-ollama"', 'default = "ghost"')
        with self.assertRaises(BackendConfigError):
            load_pool(_write_pool(self.tmp, bad))


class TokenTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        self.pool = load_pool(_write_pool(self.tmp))

    def test_token_read_from_auth_env(self) -> None:
        with patch.dict(os.environ, {"AM4_OXEN_TOKEN": "sk-abc"}):
            self.assertEqual(self.pool.by_name("am4-oxen").token(), "sk-abc")

    def test_missing_token_is_none_not_error(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(self.pool.by_name("am4-oxen").token())

    def test_backend_without_auth_env_has_no_token(self) -> None:
        self.assertIsNone(self.pool.by_name("omen-ollama").token())


class SelectBackendTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        self.pool = load_pool(_write_pool(self.tmp))

    def test_no_signal_returns_default(self) -> None:
        chosen, reason, occ = select_backend(self.pool)
        self.assertEqual(chosen.name, "omen-ollama")
        self.assertEqual(reason, "default")
        self.assertEqual(occ["occupancy"], "available")

    def test_task_tag_routes_to_oxen(self) -> None:
        chosen, reason, occ = select_backend(self.pool, task="research")
        self.assertEqual(chosen.name, "am4-oxen")
        self.assertEqual(reason, "tag:research")
        self.assertEqual(occ["occupancy"], "available")

    def test_pinned_backend_name(self) -> None:
        chosen, reason, occ = select_backend(self.pool, backend="am4-oxen")
        self.assertEqual(chosen.name, "am4-oxen")
        self.assertEqual(reason, "pinned:am4-oxen")
        self.assertEqual(occ["occupancy"], "available")

    def test_unknown_backend_name_raises(self) -> None:
        with self.assertRaises(BackendConfigError):
            select_backend(self.pool, backend="nope")

    def test_unmatched_tag_falls_back_to_default(self) -> None:
        chosen, reason, occ = select_backend(self.pool, task="does-not-exist")
        self.assertEqual(chosen.name, "omen-ollama")
        self.assertEqual(reason, "default")

    def test_explicit_tags_list_matched(self) -> None:
        chosen, reason, occ = select_backend(self.pool, tags=["big-context"])
        self.assertEqual(chosen.name, "am4-oxen")
        self.assertEqual(reason, "tag:big-context")

    def test_frontier_tag_routes_to_gemini(self) -> None:
        chosen, reason, occ = select_backend(self.pool, task="cloud-overflow")
        self.assertEqual(chosen.name, "gcp-gemini")
        self.assertEqual(reason, "tag:cloud-overflow")


class OccupancySkipTests(TestCase):
    """P2: a tag-routed candidate that is busy (or unknown) is skipped."""

    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        self.pool = load_pool(_write_pool(self.tmp))

    def test_busy_tag_candidate_falls_back_to_default(self) -> None:
        def occ_check(name: str) -> dict:
            return {"occupancy": "busy"} if name == "am4-oxen" else {"occupancy": "available"}
        chosen, reason, occ = select_backend(self.pool, task="research", occupancy_check=occ_check)
        self.assertEqual(chosen.name, "omen-ollama")
        self.assertEqual(reason, "default")

    def test_unknown_tag_candidate_treated_as_busy_and_skipped(self) -> None:
        def occ_check(name: str) -> dict:
            return {"occupancy": "unknown"} if name == "am4-oxen" else {"occupancy": "available"}
        chosen, reason, occ = select_backend(self.pool, task="research", occupancy_check=occ_check)
        self.assertEqual(chosen.name, "omen-ollama")

    def test_available_tag_candidate_is_chosen_and_occupancy_reported(self) -> None:
        def occ_check(name: str) -> dict:
            return {"occupancy": "available"}
        chosen, reason, occ = select_backend(self.pool, task="research", occupancy_check=occ_check)
        self.assertEqual(chosen.name, "am4-oxen")
        self.assertEqual(occ["occupancy"], "available")

    def test_pinned_backend_never_occupancy_skipped_even_when_busy(self) -> None:
        def occ_check(name: str) -> dict:
            return {"occupancy": "busy"}
        chosen, reason, occ = select_backend(self.pool, backend="am4-oxen", occupancy_check=occ_check)
        self.assertEqual(chosen.name, "am4-oxen")
        self.assertEqual(reason, "pinned:am4-oxen")
        self.assertEqual(occ["occupancy"], "busy")

    def test_pinned_backend_unknown_occupancy_still_routes(self) -> None:
        def occ_check(name: str) -> dict:
            return {"occupancy": "unknown"}
        chosen, reason, occ = select_backend(self.pool, backend="am4-oxen", occupancy_check=occ_check)
        self.assertEqual(chosen.name, "am4-oxen")

    def test_no_occupancy_check_injected_behaves_like_p1(self) -> None:
        chosen, reason, occ = select_backend(self.pool, task="research")
        self.assertEqual(chosen.name, "am4-oxen")
        self.assertEqual(occ["occupancy"], "available")


class PackagedPoolTests(TestCase):
    """The real hearth/etc/backends.toml must parse and declare the packaged backends."""

    def test_packaged_pool_is_valid(self) -> None:
        pool = load_pool()  # no env, no arg -> packaged default
        self.assertIsInstance(pool, Pool)
        names = {b.name for b in pool.backends}
        self.assertIn("omen-ollama", names)
        self.assertIn("am4-oxen", names)
        self.assertIn("am4-moe", names)
        self.assertIn("gcp-gemini", names)
        self.assertIn("gcp-gemini-pro", names)
        self.assertEqual(pool.by_name("am4-oxen").api, "openai")
        self.assertEqual(pool.by_name("am4-moe").api, "openai")
        self.assertEqual(pool.by_name("gcp-gemini").api, "gemini")
        self.assertEqual(pool.by_name("gcp-gemini-pro").api, "gemini")
        self.assertEqual(pool.by_name("gcp-gemini").settings.get("max_tokens"), 16384)
        self.assertEqual(pool.by_name("gcp-gemini-pro").settings.get("max_tokens"), 16384)
        self.assertEqual(pool.by_name("am4-moe").settings.get("max_tokens"), 12288)
        # The residency handover: the resident moe carries the opportunistic
        # tags; the single-card planner rung is pin-only (no shared tags left).
        self.assertIn("big-context", pool.by_name("am4-moe").tags)
        self.assertNotIn("big-context", pool.by_name("am4-oxen").tags)
        self.assertEqual(pool.by_name("am4-oxen").revive, None)
        # A1: every packaged rung declares a payload budget.
        for name in ("omen-ollama", "am4-oxen", "am4-moe", "gcp-gemini", "gcp-gemini-pro"):
            self.assertIsNotNone(pool.by_name(name).context_bytes(), name)


class ContextBytesTests(TestCase):
    def test_context_bytes_valid(self) -> None:
        b = Backend("test", "ep", "ollama", settings={"context_bytes": 100})
        self.assertEqual(b.context_bytes(), 100)

    def test_context_bytes_missing(self) -> None:
        b = Backend("test", "ep", "ollama")
        self.assertIsNone(b.context_bytes())

    def test_context_bytes_malformed(self) -> None:
        b = Backend("test", "ep", "ollama", settings={"context_bytes": "foo"})
        self.assertIsNone(b.context_bytes())
        b2 = Backend("test", "ep", "ollama", settings={"context_bytes": -10})
        self.assertIsNone(b2.context_bytes())


_SIZED_POOL_TOML = textwrap.dedent("""
    default = "omen-ollama"

    [[backend]]
    name = "omen-ollama"
    endpoint = "http://127.0.0.1:11434"
    api = "ollama"
    tags = ["default", "code"]
    settings = { context_bytes = 1000 }

    [[backend]]
    name = "am4-oxen"
    endpoint = "http://10.0.0.1:8090"
    api = "openai"
    tags = ["research", "big-context"]
    settings = { context_bytes = 5000 }

    [[backend]]
    name = "gcp-gemini"
    endpoint = "https://aiplatform.googleapis.com"
    api = "gemini"
    tags = ["frontier", "cloud-overflow"]
    settings = { context_bytes = 10000 }
""")


class PayloadAwareRoutingTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        self.pool = load_pool(_write_pool(self.tmp, _SIZED_POOL_TOML))

    def test_size_skip_tag_candidate(self) -> None:
        chosen, reason, occ = select_backend(self.pool, task="code", payload_bytes=2000)
        self.assertEqual(chosen.name, "am4-oxen")
        self.assertEqual(reason, "payload:big-context:am4-oxen")

    def test_default_overflow_to_big_context(self) -> None:
        chosen, reason, occ = select_backend(self.pool, payload_bytes=2000)
        self.assertEqual(chosen.name, "am4-oxen")
        self.assertEqual(reason, "payload:big-context:am4-oxen")

    def test_default_overflow_big_context_busy_to_cloud_overflow(self) -> None:
        def occ_check(name: str) -> dict:
            return {"occupancy": "busy"} if name == "am4-oxen" else {"occupancy": "available"}
        chosen, reason, occ = select_backend(self.pool, payload_bytes=8000, occupancy_check=occ_check)
        self.assertEqual(chosen.name, "gcp-gemini")
        self.assertEqual(reason, "payload:cloud-overflow:gcp-gemini")

    def test_default_overflow_nothing_fits(self) -> None:
        with self.assertRaises(BackendRoutingRefusal) as ctx:
            select_backend(self.pool, payload_bytes=20000)
        refusal = ctx.exception.as_dict()
        self.assertEqual(refusal["reason"], "payload_over_budget_no_eligible_backend")
        self.assertEqual(refusal["payload_bytes"], 20000)
        self.assertEqual(refusal["required_context_bytes"], 20000)
        self.assertEqual(refusal["default_backend"], "omen-ollama")
        self.assertNotEqual(refusal["reason"], "default:overflow")

    def test_default_overflow_all_qualifying_rungs_unknown_is_refused(self) -> None:
        def occ_check(name: str) -> dict:
            return {"occupancy": "unknown"}

        with self.assertRaises(BackendRoutingRefusal) as ctx:
            select_backend(self.pool, payload_bytes=2000, occupancy_check=occ_check)
        attempted = ctx.exception.as_dict()["attempted"]
        self.assertIn("am4-oxen", {row["name"] for row in attempted})
        self.assertIn("gcp-gemini", {row["name"] for row in attempted})
        self.assertTrue(all(row["occupancy"] != "available" for row in attempted))

    def test_default_is_never_selected_after_context_failure(self) -> None:
        with self.assertRaises(BackendRoutingRefusal):
            select_backend(self.pool, payload_bytes=20000)

    def test_exclude_candidate(self) -> None:
        chosen, reason, occ = select_backend(self.pool, task="code", exclude={"omen-ollama"})
        self.assertEqual(chosen.name, "am4-oxen")
        self.assertEqual(reason, "fallback:big-context:am4-oxen")

    def test_small_payload_still_routes_default(self) -> None:
        chosen, reason, occ = select_backend(self.pool, payload_bytes=500)
        self.assertEqual(chosen.name, "omen-ollama")
        self.assertEqual(reason, "default")

    def test_backward_compat_no_context_bytes(self) -> None:
        toml = textwrap.dedent("""
            default = "omen-ollama"
            [[backend]]
            name = "omen-ollama"
            endpoint = "http://127.0.0.1:11434"
            api = "ollama"
            tags = ["default"]
        """)
        pool = load_pool(_write_pool(self.tmp, toml))
        chosen, reason, occ = select_backend(pool, payload_bytes=999999)
        self.assertEqual(chosen.name, "omen-ollama")
        self.assertEqual(reason, "default")


class EscalationTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        toml = textwrap.dedent("""
            default = "b1"
            [[backend]]
            name = "b1"
            endpoint = "http://b1"
            api = "ollama"
            models = ["m1"]
            tags = ["code"]
            [[backend]]
            name = "b2"
            endpoint = "http://b2"
            api = "ollama"
            models = ["m2"]
            tags = ["cloud-overflow"]
        """)
        self.pool_path = _write_pool(self.tmp, toml)
        os.environ["HEARTH_BACKENDS"] = str(self.pool_path)

    def tearDown(self) -> None:
        os.environ.pop("HEARTH_BACKENDS", None)

    @patch("hearth.toolsurface.inference._post")
    def test_non_pinned_failure_escalates(self, mock_post) -> None:
        mock_post.side_effect = [
            (None, "connection error b1"),
            ({"response": "ok b2", "model": "m2"}, None),
        ]
        res = local_generate("test prompt", task="code")
        self.assertTrue(res.get("ok"))
        self.assertEqual(res["backend"], "b2")
        self.assertEqual(res["routed_by"], "escalation:b1->b2")
        self.assertEqual(res["escalation"], {"from": "b1", "error": "connection error b1"})
        self.assertEqual(res["model"], "m2")

    @patch("hearth.toolsurface.inference._post")
    def test_pinned_failure_does_not_escalate(self, mock_post) -> None:
        mock_post.side_effect = [(None, "connection error b1")]
        res = local_generate("test prompt", backend="b1")
        self.assertFalse(res.get("ok"))
        self.assertEqual(res["backend"], "b1")
        self.assertEqual(res["routed_by"], "pinned:b1")
        self.assertNotIn("escalation", res)

    @patch("hearth.toolsurface.inference._post")
    def test_double_failure(self, mock_post) -> None:
        mock_post.side_effect = [
            (None, "error b1"),
            (None, "error b2"),
        ]
        res = local_generate("test prompt")
        self.assertFalse(res.get("ok"))
        self.assertEqual(res["backend"], "b2")
        self.assertEqual(res["routed_by"], "escalation:b1->b2")
        self.assertEqual(res["error"], "error b2")
        self.assertEqual(res["escalation"], {"from": "b1", "error": "error b1"})

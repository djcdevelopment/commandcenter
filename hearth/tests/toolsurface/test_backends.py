from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface.backends import (
    BackendConfigError,
    Pool,
    load_pool,
    select_backend,
)

_POOL_TOML = textwrap.dedent("""
    default = "omen-ollama"

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
        self.assertEqual(len(pool.backends), 2)
        oxen = pool.by_name("am4-oxen")
        self.assertEqual(oxen.api, "openai")
        self.assertEqual(oxen.auth_env, "AM4_OXEN_TOKEN")
        self.assertEqual(oxen.models, ("qwen3-30b",))
        self.assertEqual(oxen.occupancy, {"conductor_worker": "am4-worker-1"})

    def test_missing_file_falls_back_to_omen_ollama(self) -> None:
        pool = load_pool(self.tmp / "does-not-exist.toml")
        self.assertEqual(pool.default, "omen-ollama")
        self.assertEqual([b.name for b in pool.backends], ["omen-ollama"])

    def test_env_var_overrides_path_arg(self) -> None:
        declared = _write_pool(self.tmp)
        with patch.dict(os.environ, {"HEARTH_BACKENDS": str(declared)}):
            pool = load_pool()  # no arg -> env wins
        self.assertEqual(len(pool.backends), 2)

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
    """The real hearth/etc/backends.toml must parse and declare the two backends."""

    def test_packaged_pool_is_valid(self) -> None:
        pool = load_pool()  # no env, no arg -> packaged default
        self.assertIsInstance(pool, Pool)
        names = {b.name for b in pool.backends}
        self.assertIn("omen-ollama", names)
        self.assertIn("am4-oxen", names)
        self.assertEqual(pool.by_name("am4-oxen").api, "openai")

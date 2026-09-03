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
from hearth.errortax import classify_error

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

    def test_by_model_returns_declared_providers(self) -> None:
        pool = load_pool(_write_pool(self.tmp))
        self.assertEqual(
            ["am4-oxen"], [backend.name for backend in pool.by_model("qwen3-30b")]
        )
        self.assertEqual((), pool.by_model("missing"))

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

    def test_explicit_model_routes_to_its_declared_provider(self) -> None:
        chosen, reason, _ = select_backend(self.pool, model="qwen3-30b")
        self.assertEqual("am4-oxen", chosen.name)
        self.assertEqual("model:qwen3-30b", reason)

    def test_unknown_model_fails_closed(self) -> None:
        with self.assertRaisesRegex(BackendConfigError, "no provider declares"):
            select_backend(self.pool, model="missing")

    def test_pinned_backend_must_offer_requested_model(self) -> None:
        with self.assertRaisesRegex(BackendConfigError, "does not provide"):
            select_backend(self.pool, backend="am4-oxen", model="gemini-3.5-flash")

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

    def test_exclusive_gpu_tenancy_overrides_a_pin(self) -> None:
        def occ_check(name: str) -> dict:
            return {
                "occupancy": "busy", "exclusive": True,
                "detail": "omen-b70-pool belongs to imagegen session session_a",
            }
        with self.assertRaises(BackendConfigError) as context:
            select_backend(self.pool, backend="omen-ollama", occupancy_check=occ_check)
        self.assertIn("exclusive GPU tenancy", str(context.exception))

    def test_a_probe_failure_is_named_distinctly_from_a_real_image_session(self) -> None:
        """Both fail closed. They need different fixes, so the error must say which.

        probe_omen_arc_slots returns exclusive:True when the tenancy STORE is unreadable --
        a locked SQLite file or a missing hearth/var/execution. That used to surface on the
        door's default rung as "unavailable during exclusive GPU tenancy", an error with no
        visible connection to its cause.
        """
        def probe_failed(name: str) -> dict:
            return {
                "occupancy": "unknown", "exclusive": True,
                "exclusive_reason": "tenancy_probe_failed",
                "detail": "GPU tenancy store unreadable",
            }
        with self.assertRaises(BackendConfigError) as context:
            select_backend(self.pool, backend="omen-ollama", occupancy_check=probe_failed)
        self.assertIn("tenancy_probe_failed", str(context.exception))

        def session_active(name: str) -> dict:
            return {
                "occupancy": "busy", "exclusive": True,
                "exclusive_reason": "image_session_active",
                "detail": "owned by imagegen session session_a",
            }
        with self.assertRaises(BackendConfigError) as context:
            select_backend(self.pool, backend="omen-ollama", occupancy_check=session_active)
        self.assertIn("image_session_active", str(context.exception))

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
        for name in ("omen-arc", "omen-arc-oss", "omen-ollama",
                     "am4-oxen", "am4-moe", "gcp-gemini", "gcp-gemini-pro"):
            self.assertIn(name, names)
        self.assertEqual(pool.by_name("omen-arc").api, "openai")
        self.assertEqual(pool.by_name("omen-arc-oss").api, "openai")
        self.assertEqual(pool.by_name("gcp-gemini").api, "gemini")
        self.assertEqual(pool.by_name("gcp-gemini-pro").api, "gemini")
        self.assertEqual(pool.by_name("gcp-gemini").settings.get("max_tokens"), 16384)
        self.assertEqual(pool.by_name("gcp-gemini-pro").settings.get("max_tokens"), 16384)
        # ADR-0034: omen-arc is the door default and carries the opportunistic
        # tags; the banked-fire 120B and every tombstone rung are pin-only.
        self.assertEqual(pool.default, "omen-arc")
        self.assertIn("big-context", pool.by_name("omen-arc").tags)
        self.assertIn("research", pool.by_name("omen-arc").tags)
        self.assertEqual(pool.by_name("omen-arc-oss").tags, ())
        self.assertEqual(pool.by_name("am4-moe").tags, ())     # ☠ tombstone
        self.assertEqual(pool.by_name("am4-oxen").tags, ())    # ☠ tombstone
        self.assertEqual(pool.by_name("omen-ollama").tags, ()) # demoted (CPU-only on Arc)
        self.assertEqual(pool.by_name("am4-oxen").revive, None)
        # A1: every packaged rung declares a payload budget.
        for name in ("omen-arc", "omen-arc-oss", "omen-ollama",
                     "am4-oxen", "am4-moe", "gcp-gemini", "gcp-gemini-pro"):
            self.assertIsNotNone(pool.by_name(name).context_bytes(), name)
        # omen-arc serves -c 131072 across -np 2 => 64k tokens/slot, ≈57k of it
        # as payload at the ≈4 bytes/token convention (same arithmetic the old
        # am4 rungs used; those pins stay to keep the tombstones honest).
        # Widened from 16k/slot on 2026-08-24 for Hermes Agent's 64000-token
        # floor; must track serve-arc.cmd's -c/-np, since llama-server silently
        # truncates over-long prompts rather than rejecting them.
        self.assertEqual(pool.by_name("omen-arc").context_bytes(), 229376)
        self.assertEqual(pool.by_name("omen-arc-oss").context_bytes(), 57344)
        self.assertEqual(pool.by_name("am4-oxen").context_bytes(), 57344)
        self.assertEqual(pool.by_name("am4-moe").context_bytes(), 57344)


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


class PinnedPayloadBudgetTests(TestCase):
    """A pin overrides occupancy, but not the rung's declared context budget."""

    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        self.pool = load_pool(_write_pool(self.tmp, _SIZED_POOL_TOML))

    def test_pinned_over_budget_is_refused(self) -> None:
        with self.assertRaises(BackendRoutingRefusal) as ctx:
            select_backend(self.pool, backend="am4-oxen", payload_bytes=8000)
        refusal = ctx.exception.as_dict()
        self.assertEqual(refusal["reason"], "payload_over_budget_for_pinned_backend")
        self.assertEqual(refusal["payload_bytes"], 8000)
        # One refused rung, not a walked ladder.
        self.assertEqual(refusal["attempted"], [{
            "name": "am4-oxen",
            "context_bytes": 5000,
            "occupancy": "not_checked",
            "rejection_reason": "payload_over_budget",
            "pinned": True,
        }])

    def test_pin_refusal_does_not_leak_onto_the_class_default(self) -> None:
        # reason_code shadows a class attribute; a pinned refusal must not
        # rewrite the code every later ladder refusal reports.
        with self.assertRaises(BackendRoutingRefusal):
            select_backend(self.pool, backend="am4-oxen", payload_bytes=8000)
        with self.assertRaises(BackendRoutingRefusal) as ladder:
            select_backend(self.pool, payload_bytes=20000)
        self.assertEqual(ladder.exception.as_dict()["reason"],
                         "payload_over_budget_no_eligible_backend")

    def test_both_refusal_codes_classify_as_routing_refusal(self) -> None:
        # The gateway re-derives error_code from the message text rather than
        # reading the result's own error_code, so a reason the taxonomy does not
        # name would ledger a refusal as "other" and stop it being counted.
        refusals = []
        for kwargs in (dict(backend="am4-oxen", payload_bytes=8000),
                       dict(payload_bytes=20000)):
            with self.assertRaises(BackendRoutingRefusal) as ctx:
                select_backend(self.pool, **kwargs)
            refusals.append(ctx.exception)
        self.assertEqual(
            ["routing_refusal", "routing_refusal"],
            [classify_error(str(exc)) for exc in refusals],
        )
        # str() carries the numbers, so a boundary that only flattens the
        # exception still reports them (ExecutionService admission does exactly this).
        self.assertIn("8000 bytes", str(refusals[0]))
        self.assertIn("am4-oxen (5000 B", str(refusals[0]))

    def test_pinned_exactly_at_budget_routes(self) -> None:
        # The comparison is strictly greater-than: a payload equal to the
        # declared budget is inside it.
        chosen, reason, occ = select_backend(self.pool, backend="am4-oxen", payload_bytes=5000)
        self.assertEqual(chosen.name, "am4-oxen")
        self.assertEqual(reason, "pinned:am4-oxen")

    def test_pin_without_payload_bytes_is_unconditional(self) -> None:
        # build_requests routes pins without ever passing a payload size; that
        # path must keep its historical behavior.
        chosen, reason, occ = select_backend(self.pool, backend="am4-oxen")
        self.assertEqual(chosen.name, "am4-oxen")
        self.assertEqual(reason, "pinned:am4-oxen")

    def test_pinned_rung_declaring_no_context_bytes_still_routes(self) -> None:
        # _POOL_TOML (the _write_pool default) declares no settings block, so
        # context_bytes() is None there — an undeclared budget stays unlimited.
        pool = load_pool(_write_pool(self.tmp))
        chosen, reason, occ = select_backend(pool, backend="omen-ollama", payload_bytes=999999)
        self.assertEqual(chosen.name, "omen-ollama")
        self.assertEqual(reason, "pinned:omen-ollama")

    def test_over_budget_pin_is_refused_without_probing_occupancy(self) -> None:
        # The payload is decided before the occupancy probe, so an unreachable
        # rung costs no SSH/HTTP round trip to refuse.
        probed: list[str] = []

        def occ_check(name: str) -> dict:
            probed.append(name)
            return {"occupancy": "busy"}

        with self.assertRaises(BackendRoutingRefusal):
            select_backend(self.pool, backend="am4-oxen", payload_bytes=8000,
                           occupancy_check=occ_check)
        self.assertEqual(probed, [])

    def test_busy_pin_within_budget_still_routes(self) -> None:
        # The occupancy override itself is untouched: a busy pin still dispatches
        # and waits in the provider's own queue.
        chosen, reason, occ = select_backend(
            self.pool, backend="am4-oxen", payload_bytes=4000,
            occupancy_check=lambda name: {"occupancy": "busy"})
        self.assertEqual(chosen.name, "am4-oxen")
        self.assertEqual(occ["occupancy"], "busy")

    def test_model_mismatch_is_raised_before_payload_budget(self) -> None:
        with self.assertRaises(BackendConfigError) as ctx:
            select_backend(self.pool, backend="omen-ollama", model="ghost",
                           payload_bytes=999999)
        # A wrong model is a plain config error, not a routing refusal — the
        # caller's first problem is that the rung cannot serve that model at all.
        self.assertNotIsInstance(ctx.exception, BackendRoutingRefusal)
        self.assertIn("does not provide model", str(ctx.exception))


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


_RETIRED_POOL_TOML = textwrap.dedent("""
    default = "omen-arc"

    [[backend]]
    name = "omen-arc"
    endpoint = "http://127.0.0.1:8082"
    api = "openai"
    models = ["qwen3-30b-a3b"]
    tags = ["default"]

    [[backend]]
    name = "am4-moe"
    endpoint = "http://100.116.82.60:8082"
    api = "openai"
    models = ["gpt-oss-120b"]
    tags = []
    retired = true
""")


class RetiredBackendTests(TestCase):
    """A tombstone stays routable but stops being advertised."""

    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        self.pool_path = _write_pool(self.tmp, _RETIRED_POOL_TOML)

    def test_retired_defaults_to_false_and_parses_when_declared(self) -> None:
        pool = load_pool(self.pool_path)
        self.assertFalse(pool.by_name("omen-arc").retired)
        self.assertTrue(pool.by_name("am4-moe").retired)

    def test_retired_rung_is_still_pinnable(self) -> None:
        # The pin must keep reaching the rung so it fails with that rung's own
        # error, not "unknown backend" — the tombstone is documentation, and an
        # operator who pins it deliberately deserves the specific failure.
        pool = load_pool(self.pool_path)
        chosen, reason, _ = select_backend(pool, backend="am4-moe")
        self.assertEqual(chosen.name, "am4-moe")
        self.assertEqual(reason, "pinned:am4-moe")

    def test_retired_rung_is_withheld_from_the_provider_projection(self) -> None:
        from hearth.toolsurface import execution_control

        with patch.object(
            execution_control, "load_pool", lambda: load_pool(self.pool_path)
        ):
            names = [p["name"] for p in execution_control.list_execution_providers()]
        self.assertEqual(names, ["omen-arc"])

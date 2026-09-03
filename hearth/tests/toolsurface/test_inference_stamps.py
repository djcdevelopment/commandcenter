"""P8 — dispatch stamps: every local_generate result carries `pool_config_hash`
(which pool declaration routed it) and `rung_state` (the rung's ADR-0044 verdict
as of dispatch). Stamps are provenance: they must never break a dispatch, never
invent a reading for a rung the baselines do not name, and must survive the
kernel ledger's row validation unchanged.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from hearth.health.rungstate import NOTE
from hearth.kernel.ledger import new_event, validate_event
from hearth.toolsurface import backends as backends_mod
from hearth.toolsurface import inference
from hearth.toolsurface.backends import pool_config_hash
from hearth.toolsurface.inference import local_generate

_HEX12 = re.compile(r"^[0-9a-f]{12}$")

# A hermetic pool: the baseline-named rung is the default (so escalation leaves
# it), and the rescuer is a rung the baselines never mention. Ollama api on both
# so no auth env is needed; `_post` is patched, nothing touches the network.
_POOL = textwrap.dedent("""
    default = "omen-arc"
    [[backend]]
    name = "omen-arc"
    endpoint = "http://127.0.0.1:8082"
    api = "ollama"
    models = ["m1"]
    tags = ["code"]
    [[backend]]
    name = "cloud"
    endpoint = "http://cloud"
    api = "ollama"
    models = ["m2"]
    tags = ["cloud-overflow"]
""")

_BASELINES = {
    "contract_version": "ff-rate-baselines.v1",
    "rungs": {
        "omen-arc": {
            "port": 8082,
            "baseline_decode_tok_s": 106.0,
            "baseline_epoch": "2026-08-29T18:22 incumbent epoch",
            "acceptance_envelope": {"fail_below_frac": 0.8, "warn_below_frac": 0.9},
        }
    },
}

_AVAILABLE = patch("hearth.toolsurface.inference.check_occupancy",
                   return_value={"occupancy": "available"})


def _row(age_s: float, **extra) -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()
    row = {"ts": ts, "probe": "ARC-KEEPALIVE", "port": 8082, "ok": True, "wall_ms": 50.0,
           "prompt_n": 1, "prompt_ms": 10.0, "predicted_n": 1, "predicted_ms": 0.0,
           "prefill_stall": False}
    row.update(extra)
    return row


def write_fixture_tree(root: Path, deep_tok_s: float = 65.0) -> None:
    """A baselines file plus a keep-alive tail whose newest deep row reads `deep_tok_s`."""
    (root / "campaign" / "ff-probes").mkdir(parents=True, exist_ok=True)
    (root / "hearth" / "var").mkdir(parents=True, exist_ok=True)
    (root / "campaign" / "ff-probes" / "rate-baselines.json").write_text(
        json.dumps(_BASELINES), encoding="utf-8")
    rows = [_row(100.0, predicted_n=32, predicted_ms=32000.0 / deep_tok_s,
                 decode_tok_s=deep_tok_s)]
    rows += [_row(age) for age in (65.0, 35.0, 5.0)]
    (root / "hearth" / "var" / "arc-keepalive.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


class _StampFixture(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write_fixture_tree(self.tmp)
        self.pool_path = self.tmp / "backends.toml"
        self.pool_path.write_text(_POOL, encoding="utf-8")
        self.enterContext(patch.dict(os.environ, {"HEARTH_BACKENDS": str(self.pool_path)}))
        self.enterContext(_AVAILABLE)
        self._saved_root = inference.RUNG_STATE_ROOT
        inference.RUNG_STATE_ROOT = self.tmp
        inference._RUNG_STATE_CACHE.clear()

    def tearDown(self) -> None:
        inference.RUNG_STATE_ROOT = self._saved_root
        inference._RUNG_STATE_CACHE.clear()


class DispatchStampTests(_StampFixture):
    @patch("hearth.toolsurface.inference._post")
    def test_degraded_rung_is_stamped_on_an_ok_result(self, mock_post) -> None:
        """The lab's failure mode: the call succeeds, the rung is quietly 39% down."""
        mock_post.return_value = ({"response": "fine", "model": "m1"}, None)
        result = local_generate("q", backend="omen-arc")

        self.assertTrue(result["ok"])
        state = result["rung_state"]
        self.assertEqual(state["verdict"], "degraded")
        self.assertEqual(state["observed_tok_s"], 65.0)
        self.assertEqual(state["baseline_tok_s"], 106.0)
        self.assertEqual(state["note"], NOTE)  # ADR-0044: never names a regime
        self.assertRegex(result["pool_config_hash"], _HEX12)
        self.assertEqual(result["pool_config_hash"], pool_config_hash())

    @patch("hearth.toolsurface.inference._post")
    def test_rung_absent_from_baselines_gets_no_reading(self, mock_post) -> None:
        """No baseline is absence, not a verdict: a cloud call carries no OMEN health line."""
        mock_post.return_value = ({"response": "fine", "model": "m2"}, None)
        result = local_generate("q", backend="cloud")

        self.assertTrue(result["ok"])
        self.assertIn("rung_state", result)
        self.assertIsNone(result["rung_state"])
        self.assertRegex(result["pool_config_hash"], _HEX12)

    @patch("hearth.toolsurface.inference._post")
    def test_stamp_failure_never_breaks_the_dispatch(self, mock_post) -> None:
        mock_post.return_value = ({"response": "fine", "model": "m1"}, None)
        with patch("hearth.toolsurface.inference.live_rung_state",
                   side_effect=RuntimeError("keepalive exploded")), \
                patch("hearth.toolsurface.inference.pool_config_hash",
                      side_effect=RuntimeError("stat exploded")):
            result = local_generate("q", backend="omen-arc")

        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "fine")
        self.assertIsNone(result["rung_state"])
        self.assertIsNone(result["pool_config_hash"])

    @patch("hearth.toolsurface.inference._post")
    def test_failed_dispatch_is_stamped_too(self, mock_post) -> None:
        """A pin never escalates; the failure row still says how the rung looked."""
        mock_post.return_value = (None, "connection refused")
        result = local_generate("q", backend="omen-arc")

        self.assertFalse(result["ok"])
        self.assertEqual(result["rung_state"]["verdict"], "degraded")
        self.assertRegex(result["pool_config_hash"], _HEX12)

    @patch("hearth.toolsurface.inference._post")
    def test_escalated_result_carries_the_rescuers_stamps(self, mock_post) -> None:
        mock_post.side_effect = [
            (None, "connection error omen-arc"),
            ({"response": "ok cloud", "model": "m2"}, None),
        ]
        result = local_generate("q", task="code")

        self.assertTrue(result["ok"])
        self.assertEqual(result["routed_by"], "escalation:omen-arc->cloud")
        self.assertEqual(result["backend"], "cloud")
        self.assertIsNone(result["rung_state"])  # the rescuer has no baseline
        self.assertRegex(result["pool_config_hash"], _HEX12)

    @patch("hearth.toolsurface.inference._post")
    def test_rung_state_is_cached_and_handed_out_as_a_copy(self, mock_post) -> None:
        mock_post.return_value = ({"response": "fine", "model": "m1"}, None)
        fake_state = {"rung": "omen-arc", "verdict": "warn", "observed_tok_s": 92.0,
                      "baseline_tok_s": 106.0, "note": NOTE}
        with patch("hearth.toolsurface.inference.live_rung_state",
                   return_value=fake_state) as reader:
            first = local_generate("q", backend="omen-arc")
            first["rung_state"]["verdict"] = "tampered"
            second = local_generate("q", backend="omen-arc")

        self.assertEqual(reader.call_count, 1)
        self.assertEqual(second["rung_state"]["verdict"], "warn")
        self.assertIsNot(first["rung_state"], second["rung_state"])

    @patch("hearth.toolsurface.inference._post")
    def test_cache_expires_after_the_ttl(self, mock_post) -> None:
        mock_post.return_value = ({"response": "fine", "model": "m1"}, None)
        with patch("hearth.toolsurface.inference.live_rung_state",
                   return_value={"verdict": "at_rate"}) as reader:
            local_generate("q", backend="omen-arc")
            stamped_at, cached = inference._RUNG_STATE_CACHE["omen-arc"]
            inference._RUNG_STATE_CACHE["omen-arc"] = (
                stamped_at - inference.RUNG_STATE_TTL_S - 1, cached)
            local_generate("q", backend="omen-arc")
        self.assertEqual(reader.call_count, 2)

    def test_ask_path_is_not_a_dispatch_and_is_not_stamped(self) -> None:
        result = local_generate("q", quality="best")
        self.assertTrue(result.get("ask"))
        self.assertNotIn("rung_state", result)
        self.assertNotIn("pool_config_hash", result)

    @patch("hearth.toolsurface.inference._post")
    def test_kernel_ledger_row_still_validates(self, mock_post) -> None:
        """The gateway lifts only string provenance into the hearth-event.v1 row; the
        dict-valued stamp rides the result digest and must not trip validation."""
        mock_post.return_value = ({"response": "fine", "model": "m1"}, None)
        result = local_generate("q", backend="omen-arc")
        self.assertIsInstance(result["rung_state"], dict)

        raw = (result.get("backend"), result.get("routed_by"), result.get("occupancy"))
        backend, routed_by, occupancy = (v if isinstance(v, str) else None for v in raw)
        event = new_event({"id": "claude-frontier", "runner_class": "frontier", "node": "omen"},
                          "local_generate", args={"prompt": "q", "backend": "omen-arc"},
                          result=result, ok=True, duration_ms=12.5, task_class="inference",
                          model=result["model"], backend=backend, routed_by=routed_by,
                          occupancy=occupancy)
        validate_event(event)  # raises on a bad row
        self.assertEqual(event["backend"], "omen-arc")
        self.assertEqual(event["occupancy"], "available")


class PoolConfigHashTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {}, clear=False))
        os.environ.pop("HEARTH_BACKENDS", None)

    def test_hash_is_twelve_hex_chars_of_the_bytes(self) -> None:
        path = self.tmp / "pool.toml"
        path.write_text(_POOL, encoding="utf-8")
        digest = pool_config_hash(path)
        self.assertRegex(digest, _HEX12)
        import hashlib
        self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest()[:12])

    def test_comment_only_edit_changes_the_stamp(self) -> None:
        """Bytes, not the parsed pool: a ledger row names the exact declaration."""
        path = self.tmp / "pool.toml"
        path.write_text(_POOL, encoding="utf-8")
        before = pool_config_hash(path)
        path.write_text(_POOL + "\n# re-declared after the cutover\n", encoding="utf-8")
        after = pool_config_hash(path)
        self.assertNotEqual(before, after)

    def test_unchanged_file_is_served_from_the_cache(self) -> None:
        path = self.tmp / "pool.toml"
        path.write_text(_POOL, encoding="utf-8")
        first = pool_config_hash(path)
        with patch.object(Path, "read_bytes", side_effect=AssertionError("re-read")):
            second = pool_config_hash(path)
        self.assertEqual(first, second)
        self.assertIn(str(path), backends_mod._POOL_HASH_CACHE)

    def test_missing_file_is_none_not_an_error(self) -> None:
        self.assertIsNone(pool_config_hash(self.tmp / "absent.toml"))

    def test_env_override_wins_like_load_pool(self) -> None:
        path = self.tmp / "env-pool.toml"
        path.write_text(_POOL, encoding="utf-8")
        with patch.dict(os.environ, {"HEARTH_BACKENDS": str(path)}):
            self.assertEqual(pool_config_hash(), pool_config_hash(path))

    def test_packaged_default_hashes_when_no_override(self) -> None:
        self.assertRegex(pool_config_hash(), _HEX12)

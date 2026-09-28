"""Invariants of the sizing map (tools/ops/sizing_map.py), each earned by an observation on omen-linux
on 2026-09-27: the DeepAgents delivery that died on a 12-attempt budget with the 27B healthy, the
16,384-token candidate refused at the work.produce ceiling, the 9.3 s lease wait on a 3-slot dense
lane whose KV pool holds 1.5 full requests, and the payload admission that reserves no output.

The rules run over synthetic rows here (the live host is read by the tool, not the tests); every
test proves a rule fires on the bad shape and stays quiet on the good one.
"""
import importlib.util
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "sizing_map", Path(__file__).resolve().parents[3] / "tools" / "ops" / "sizing_map.py")
sm = importlib.util.module_from_spec(_SPEC); _SPEC.loader.exec_module(sm)  # type: ignore[union-attr]


def R(layer, setting, value):
    return sm.row(layer, setting, value, "test", "test", "test")


def rules(rows):
    return {v["rule"] for v in sm.invariants(rows)}


class SeatAndRungTests(unittest.TestCase):
    def test_rung_context_must_equal_the_seat_window(self) -> None:
        bad = [R("rung", "omen-dense-27b context_tokens", 65536), R("seat", "omen-vllm@0 max_model_len", 40960)]
        self.assertIn("rung-context-equals-seat-window", rules(bad))
        good = [R("rung", "omen-dense-27b context_tokens", 65536), R("seat", "omen-vllm@0 max_model_len", 65536)]
        self.assertNotIn("rung-context-equals-seat-window", rules(good))

    def test_slots_must_fit_the_kv_pool(self) -> None:
        # typical = 0.30 x window + 0.5 x reserve: 8 x (12,288 + 4,096) = 131,072 fits 133,680; 9 do not.
        rows = [R("rung", "omen-vllm parallel_slots", 9), R("rung", "omen-vllm context_tokens", 40960), R("rung", "omen-vllm max_tokens", 8192),
                R("seat", "omen-vllm@1 kv_cache_size_tokens", 133680)]
        self.assertIn("slots-fit-the-kv-pool", rules(rows))
        rows[0]["value"] = 8
        self.assertNotIn("slots-fit-the-kv-pool", rules(rows))

    def test_seat_must_admit_at_least_the_leases(self) -> None:
        rows = [R("rung", "omen-dense-27b parallel_slots", 3), R("seat", "omen-vllm@0 max_num_seqs", 2)]
        self.assertIn("seat-admits-at-least-the-leases", rules(rows))

    def test_one_drop_in_per_key(self) -> None:
        rows = [R("seat", "omen-vllm@0 OMEN_MAX_MODEL_LEN set by 2 drop-ins", "max-model-len.conf < stage2-27b-mtp.conf")]
        self.assertIn("one-drop-in-per-key", rules(rows))


class DoorTests(unittest.TestCase):
    def test_operation_ceiling_within_the_largest_rung_reserve(self) -> None:
        rows = [R("operation", "inference.generate max_tokens_ceiling", 32768), R("rung", "omen-dense-27b max_tokens", 8192)]
        self.assertIn("operation-ceiling-within-the-largest-rung-reserve", rules(rows))
        rows[0]["value"] = 8192
        self.assertNotIn("operation-ceiling-within-the-largest-rung-reserve", rules(rows))

    def test_rung_reserve_within_the_work_produce_ceiling(self) -> None:
        rows = [R("operation", "work.produce max_tokens_ceiling", 8192), R("rung", "omen-dense-27b max_tokens", 16384)]
        self.assertIn("rung-reserve-within-ceiling", rules(rows))

    def test_admission_must_reserve_output(self) -> None:
        self.assertIn("door-admission-reserves-output", rules([R("door", "payload admission rule (pin and tag route)", "payload_bytes <= context_bytes")]))
        self.assertNotIn("door-admission-reserves-output", rules([R("door", "payload admission rule (pin and tag route)", "payload_bytes <= context_bytes AND payload_bytes // 4 + max_tokens <= context_tokens")]))

    def test_deadline_covers_the_output_at_the_measured_rate(self) -> None:
        rows = [R("operation", "work.produce deadline_ceiling_s", 1200), R("operation", "work.produce max_tokens_ceiling", 16384),
                R("measured", "omen-dense-27b decode tok/s median", 10.4)]
        self.assertIn("deadline-covers-the-output", rules(rows))
        rows[0]["value"] = 2400
        self.assertNotIn("deadline-covers-the-output", rules(rows))

    def test_client_and_router_timeouts_cover_the_deadline(self) -> None:
        rows = [R("operation", "work.produce deadline_ceiling_s", 2400), R("router", "haproxy timeout server", 1200),
                R("client", "codex mcp tool_timeout_sec", 1300), R("client", "claude code hearth timeout (ms)", 1300000)]
        self.assertIn("client-timeouts-cover-the-deadline", rules(rows))
        for r in rows[1:]:
            r["value"] = 2600 if r["layer"] != "client" or "ms" not in r["setting"] else 2600000
        self.assertNotIn("client-timeouts-cover-the-deadline", rules(rows))

    def test_the_door_must_serve_calls_concurrently(self) -> None:
        self.assertIn("door-serves-calls-concurrently", rules([R("door", "gateway tool dispatch", "on the event loop (one call at a time)")]))
        self.assertNotIn("door-serves-calls-concurrently", rules([R("door", "gateway tool dispatch", "threaded (asyncio.to_thread per call)")]))

    def test_router_must_queue_overflow(self) -> None:
        self.assertIn("router-queues-overflow", rules([R("router", "haproxy timeout queue", None)]))
        self.assertNotIn("router-queues-overflow", rules([R("router", "haproxy timeout queue", 60)]))


class RunnerTests(unittest.TestCase):
    def test_runner_context_equals_the_seat(self) -> None:
        rows = [R("runner", "ROUTES.omen.context", 16384), R("rung", "omen-vllm context_tokens", 40960)]
        self.assertIn("runner-context-equals-seat", rules(rows))

    def test_wrapper_outlives_the_runner(self) -> None:
        rows = [R("runner", "RequestBudget deadline (s)", 1800), R("deepagents-lane", "RUN_TIMEOUT_S (wrapper)", 1500)]
        self.assertIn("wrapper-outlives-the-runner", rules(rows))

    def test_eviction_scales_with_context(self) -> None:
        rows = [R("runner", "tool_token_limit_before_evict", 1500), R("runner", "ROUTES.omen-dense.context", 65536)]
        self.assertIn("eviction-scales-with-context", rules(rows))
        rows[0]["value"] = 8192
        self.assertNotIn("eviction-scales-with-context", rules(rows))

    def test_guard_reads_the_field_langchain_sends(self) -> None:
        self.assertIn("guard-reads-the-field-langchain-sends", rules([R("runner", "transport output guard", "reads ('max_tokens', 'n_predict', '2048')")]))
        self.assertNotIn("guard-reads-the-field-langchain-sends", rules([R("runner", "transport output guard", "reads max_completion_tokens|max_tokens|n_predict")]))


class RenderTests(unittest.TestCase):
    def test_render_is_deterministic_and_marks_its_block(self) -> None:
        rows = [R("rung", "omen-vllm max_tokens", 8192), R("router", "haproxy timeout queue", None)]
        one, two = sm.render_tables(rows), sm.render_tables(rows)
        self.assertEqual(one, two)
        self.assertTrue(one.startswith(sm.BEGIN) and one.rstrip().endswith(sm.END))
        self.assertIn("router-queues-overflow", one)


if __name__ == "__main__":
    unittest.main()


class Am4ProfileTests(unittest.TestCase):
    """2026-09-28: AM4 serves one profile at a time (dense-tp2 or tool-pair). The rung, the seat env
    and the runner route must agree on the window, and only the live profile's aliases may be ready."""

    def test_am4_rung_window_must_match_the_seat_env(self) -> None:
        rows = [R("rung", "am4-tool-4070ti context_tokens", 32768), R("am4-seat", "am4-tool@4070ti max_model_len", 16384)]
        self.assertIn("am4-rung-context-equals-seat-window", rules(rows))
        rows[1]["value"] = 32768
        self.assertNotIn("am4-rung-context-equals-seat-window", rules(rows))

    def test_only_the_live_profiles_aliases_may_be_ready(self) -> None:
        rows = [R("am4-live", "am4 profile", "dense-tp2"), R("am4-live", "facade alias am4-dense-27b ready", True),
                R("am4-live", "facade alias am4-tool-4070ti ready", True)]
        self.assertIn("am4-profile-aliases-served", rules(rows))
        rows[2]["value"] = False
        self.assertNotIn("am4-profile-aliases-served", rules(rows))

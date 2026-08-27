from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qwen38_campaign as campaign


def response(content: str = "", tool_calls: list | None = None) -> dict:
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": content, "tool_calls": tool_calls or []},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


class SourceValidationTests(unittest.TestCase):
    def test_campaign_sources_are_balanced_and_valid(self) -> None:
        self.assertEqual([], campaign.validate_sources())
        tasks = campaign.load_json(campaign.TASKS_PATH)["tasks"]
        counts: dict[str, int] = {}
        for task in tasks:
            counts[task["family"]] = counts.get(task["family"], 0) + 1
        self.assertEqual(72, len(tasks))
        self.assertEqual({8}, set(counts.values()))
        self.assertEqual(9, len(counts))
        self.assertEqual(96, len(campaign.expected_judgment_keys()))

    def test_every_json_source_parses(self) -> None:
        for path in campaign.SOURCE_ROOT.rglob("*.json"):
            with self.subTest(path=path):
                campaign.load_json(path)

    def test_power_shell_utf8_bom_receipt_parses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"status": "passed"}).encode("utf-8"))
            self.assertEqual("passed", campaign.load_json(path)["status"])

    def test_runtime_snapshot_is_byte_locked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(campaign, "runtime_root", return_value=root):
                campaign.init_runtime()
                campaign.init_runtime()
                copied_readme = root / "control" / "README.md"
                copied_readme.write_text("drift", encoding="utf-8")
                with self.assertRaises(FileExistsError):
                    campaign.init_runtime()
                campaign.init_runtime(force=True)
                self.assertEqual(campaign.sha256_file(campaign.SOURCE_ROOT / "README.md"), campaign.sha256_file(copied_readme))

    def test_resume_retries_only_failed_transport_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.jsonl"
            path.write_text(json.dumps({"request_id": "r1", "success": False}) + "\n", encoding="utf-8")
            self.assertNotIn("r1", campaign._existing_ids(path, "success"))
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"request_id": "r1", "success": True}) + "\n")
            self.assertIn("r1", campaign._existing_ids(path, "success"))

    def test_resume_amendment_requires_unchanged_locked_inputs_and_thermal_evidence(self) -> None:
        def manifest(source_hash: str, config_hash: str = "c" * 64, task_hash: str = "d" * 64) -> dict:
            return {
                "contract_version": "qwen38-run-manifest.v1",
                "source_tree_sha256": source_hash,
                "locked_at": "2026-08-27T00:00:00Z",
                "campaign_config_sha256": config_hash,
                "task_set_sha256": task_hash,
                "engines": [
                    {
                        "role": "campaign",
                        "revision": "1" * 40,
                        "binary_sha256": "2" * 64,
                        "binary_size_bytes": 123,
                    }
                ],
                "artifacts": [
                    {
                        "id": "qwen38-27b",
                        "state": "locked",
                        "revision": "3" * 40,
                        "size_bytes": 456,
                        "parts_locked": [
                            {"path": "model.gguf", "size_bytes": 456, "sha256": "4" * 64}
                        ],
                    }
                ],
            }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archived_path = root / "old-manifest.json"
            current_path = root / "run-manifest.json"
            source_path = root / "old-source-receipt.json"
            abort_path = root / "abort.json"
            old = manifest("5" * 64)
            current = manifest("6" * 64)
            source = {
                "commandcenter_revision": "7" * 40,
                "source_tree_sha256": old["source_tree_sha256"],
            }
            abort = {
                "contract_version": "qwen38-watchdog-abort.v1",
                "stage": "qwen27-replica-production-mtp-off-p512-c4",
                "aborted_at": "2026-08-27T06:40:45Z",
                "reason": "GPU/VRAM temperature 96 C reached abort line",
                "sample": {"max_temperature_c": 96},
            }
            for path, value in (
                (archived_path, old),
                (current_path, current),
                (source_path, source),
                (abort_path, abort),
            ):
                path.write_text(json.dumps(value), encoding="utf-8")
            amendment = campaign.build_resume_amendment(
                old,
                current,
                source,
                abort,
                archived_manifest_path=archived_path,
                archived_source_receipt_path=source_path,
                abort_path=abort_path,
                current_manifest_path=current_path,
            )
            self.assertTrue(amendment["model_and_engine_identity_unchanged"])
            self.assertTrue(amendment["gate_constants_unchanged"])
            self.assertTrue(amendment["task_set_unchanged"])
            self.assertEqual(96, amendment["abort_evidence"]["max_temperature_c"])
            self.assertEqual(
                ["qwen27-replica-production", "qwen27-replica-throughput"],
                amendment["quarantined_topologies"],
            )
            # Source drift is reported rather than suppressed, and is not itself a bar.
            self.assertTrue(amendment["config_drift"]["source_tree_sha256"]["changed"])
            self.assertNotIn("unchanged_inputs_proved", amendment)

            def build(**overrides):
                return campaign.build_resume_amendment(
                    overrides.get("old", old),
                    overrides.get("current", current),
                    source,
                    overrides.get("abort", abort),
                    archived_manifest_path=archived_path,
                    archived_source_receipt_path=source_path,
                    abort_path=abort_path,
                    current_manifest_path=current_path,
                )

            # A changed assay task set makes deterministic pass rates incomparable.
            with self.assertRaisesRegex(ValueError, "task set changed"):
                build(current=manifest("6" * 64, task_hash="e" * 64))
            # Gate constants that cannot be proved unchanged must block the resume.
            with self.assertRaisesRegex(ValueError, "constants changed"):
                build(current=manifest("6" * 64, config_hash="f" * 64))
            # A stale abort receipt from before this run must not re-authorize it.
            with self.assertRaisesRegex(ValueError, "stale"):
                build(abort={**abort, "aborted_at": "2026-08-26T00:00:00Z"})
            # The deep-context abort is an accepted authorizing stage too.
            self.assertTrue(
                build(abort={**abort, "stage": "dual-context-d131072-c1"})["model_and_engine_identity_unchanged"]
            )

            current["artifacts"][0]["parts_locked"][0]["sha256"] = "8" * 64
            with self.assertRaisesRegex(ValueError, "model or engine identity changed"):
                campaign.build_resume_amendment(
                    old,
                    current,
                    source,
                    abort,
                    archived_manifest_path=archived_path,
                    archived_source_receipt_path=source_path,
                    abort_path=abort_path,
                    current_manifest_path=current_path,
                )

    def test_thermal_quarantine_resume_is_evidence_gated_and_receipt_safe(self) -> None:
        scripts = campaign.SOURCE_ROOT / "scripts"
        invoke = (scripts / "invoke-leg.ps1").read_text(encoding="utf-8")
        stage = (scripts / "03-qwen27-performance.ps1").read_text(encoding="utf-8")
        runner = (scripts / "run-campaign.ps1").read_text(encoding="utf-8")
        watchdog = (scripts / "watchdog.ps1").read_text(encoding="utf-8")
        self.assertLess(invoke.index("Test-Q38LegPassed"), invoke.index("$serverState ="))
        self.assertLess(invoke.index("Test-Q38LegPassed"), invoke.index("Remove-Item -LiteralPath $watchPassed"))
        self.assertIn("Assert-Q38ThermalQuarantineEvidence", stage)
        self.assertIn("skipped-thermal-quarantine", stage)
        self.assertIn("if (-not $AcknowledgeThermalQuarantine)", stage)
        self.assertIn("-AcknowledgeThermalQuarantine:$AcknowledgeThermalQuarantine", runner)
        self.assertIn("adapter_temperatures = $adapterTemperatures", watchdog)

    def test_leg_skip_requires_measurements_not_just_a_watchdog_receipt(self) -> None:
        lib = (campaign.SOURCE_ROOT / "scripts" / "lib.ps1").read_text(encoding="utf-8")
        invoke = (campaign.SOURCE_ROOT / "scripts" / "invoke-leg.ps1").read_text(encoding="utf-8")
        stage = (campaign.SOURCE_ROOT / "scripts" / "03-qwen27-performance.ps1").read_text(encoding="utf-8")
        # The watchdog writes a passed receipt even when the request runner died,
        # so the receipt alone must never be enough to skip a leg.
        self.assertIn("ExpectedSuccessRows", lib)
        self.assertIn("results\\requests\\{0}.jsonl", lib)
        self.assertLess(lib.index("$successful.Count -lt 1"), lib.index("return $true"))
        self.assertIn("-ExpectedSuccessRows $expectedRows", invoke)
        # -Force has to reach the per-leg gate, not just the stage receipt.
        self.assertIn("QWEN38_FORCE_LEGS", lib)
        self.assertIn("$env:QWEN38_FORCE_LEGS = '1'", stage)

    def test_deep_context_quarantine_is_config_driven_and_evidenced(self) -> None:
        config = campaign.campaign_config()
        quarantine = config["thermal_quarantine"]
        self.assertEqual([131072, 262144], list(quarantine["context_depths_infeasible"]))
        self.assertIn(quarantine["evidence_stage"], campaign.THERMAL_ABORT_STAGES)
        self.assertGreaterEqual(
            float(quarantine["observed_temperature_c"]),
            float(config["safety"]["vram_temperature_abort_c"]),
        )
        stage = (campaign.SOURCE_ROOT / "scripts" / "03-qwen27-performance.ps1").read_text(encoding="utf-8")
        # Quarantined depths must still be recorded per cell, and only under
        # explicit operator acknowledgment.
        self.assertIn("context_depths_infeasible", stage)
        self.assertIn("$AcknowledgeThermalQuarantine -and $infeasibleDepths -contains $depth", stage)
        self.assertIn("Wait-Q38ThermalHeadroom", stage)


class RequestContractTests(unittest.TestCase):
    def test_streaming_chat_reconstructs_content_usage_and_real_ttft(self) -> None:
        chunks = [
            {"choices": [{"delta": {"role": "assistant", "content": None}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "Q38-"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "ABC"}, "finish_reason": "stop"}]},
            {
                "choices": [],
                "usage": {"prompt_tokens": 20, "completion_tokens": 2},
                "timings": {"prompt_ms": 10, "predicted_ms": 20, "predicted_per_second": 100},
            },
        ]

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def __iter__(self):
                for chunk in chunks:
                    yield ("data: " + json.dumps(chunk) + "\n").encode()
                yield b"data: [DONE]\n"

        with mock.patch("urllib.request.urlopen", return_value=FakeResponse()):
            result = campaign.post_chat(
                "http://127.0.0.1:1",
                {"model": "test", "messages": []},
                None,
                1,
                stream=True,
            )
        self.assertTrue(result.ok)
        self.assertEqual("Q38-ABC", campaign.response_text(result.response))
        self.assertEqual("client_stream_first_token", result.ttft_source)
        self.assertIsNotNone(result.ttft_s)
        self.assertEqual(100, result.response["timings"]["predicted_per_second"])

    def test_request_row_uses_current_llama_mtp_timing_keys(self) -> None:
        result = campaign.HttpResult(
            True,
            200,
            {
                **response("ok"),
                "timings": {
                    "draft_n": 12,
                    "draft_n_accepted": 9,
                    "prompt_ms": 25,
                    "predicted_ms": 50,
                    "prompt_per_second": 400,
                    "predicted_per_second": 100,
                },
            },
            None,
            0.1,
            0.03,
            "client_stream_first_token",
        )
        row = campaign.make_row(
            run_id="r",
            request_key="k",
            candidate="c",
            topology="t",
            endpoint="e",
            model="m",
            test_kind="performance",
            result=result,
            started_at="2026-08-27T00:00:00Z",
            concurrency=1,
            mtp=True,
        )
        self.assertEqual(12, row["drafted_tokens"])
        self.assertEqual(9, row["accepted_tokens"])
        self.assertEqual(100, row["decode_tokens_per_s"])

    def test_retrieval_payload_embeds_a_deterministic_needle(self) -> None:
        payload, expected, position = campaign._performance_payload("m", 2048, 32, 1, "cell/request")
        self.assertTrue(expected.startswith("Q38-"))
        self.assertIn(position, {0.1, 0.5, 0.9})
        self.assertIn(expected, payload["messages"][0]["content"])

    def test_repeat_assay_has_exactly_three_unique_seeds(self) -> None:
        fake = campaign.HttpResult(
            True,
            200,
            response('{"server_1":8,"server_2":8}'),
            None,
            0.1,
        )
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(campaign, "post_chat", return_value=fake):
            output = Path(directory) / "rows.jsonl"
            args = SimpleNamespace(
                output=str(output),
                run_id="repeats",
                api_key_env="",
                endpoint=["http://unused"],
                family=None,
                task_id=["reasoning-02"],
                repeat_only=True,
                include_repeats=True,
                model="m",
                max_tokens=50,
                timeout_s=1,
                candidate="c",
                topology="t",
                concurrency=2,
                mtp=False,
                slot_depth=65536,
                parallel_slots=2,
                candidate_revision="r",
                artifact_revision="a",
                model_quant="Q4",
                placement="dual",
                shared_postload_gb=0.2,
                commit_preload_gb=50,
                commit_postload_gb=49,
                disable_thinking=True,
            )
            campaign.run_assay(args)
            rows = campaign.read_rows([output])
        self.assertEqual(3, len(rows))
        self.assertEqual(3, len({row["request_id"] for row in rows}))
        self.assertEqual({38027, 38038, 38049}, {row["seed"] for row in rows})
        self.assertTrue(all(row["thinking_disabled"] for row in rows))


class ValidatorTests(unittest.TestCase):
    def test_exact_text_is_whitespace_and_terminal_punctuation_tolerant(self) -> None:
        task = {"validator": {"type": "exact_text", "expected": "PASS"}}
        self.assertEqual((True, None), campaign.validate_task_response(task, response("  PASS.\n")))

    def test_json_fence_is_accepted_but_wrong_value_fails(self) -> None:
        task = {"validator": {"type": "json_equal", "expected": {"count": 3}}}
        self.assertEqual((True, None), campaign.validate_task_response(task, response('```json\n{"count":3}\n```')))
        valid, failure = campaign.validate_task_response(task, response('{"count":4}'))
        self.assertFalse(valid)
        self.assertEqual("json_mismatch", failure)

    def test_word_limit_and_required_terms_are_both_enforced(self) -> None:
        task = {"validator": {"type": "max_words_contains", "max_words": 6, "terms": ["spill-free"]}}
        self.assertEqual((True, None), campaign.validate_task_response(task, response("Use measured spill-free routing metadata.")))
        self.assertEqual("word_limit_exceeded", campaign.validate_task_response(task, response("Use measured spill-free routing metadata only after a complete hardware proof."))[1])
        self.assertTrue(campaign.validate_task_response(task, response("Use measured routing metadata."))[1].startswith("missing_required_terms"))

    def test_tool_call_arguments_must_match_exactly(self) -> None:
        task = {"validator": {"type": "tool_call", "name": "probe", "arguments": {"port": 8082}}}
        call = {"type": "function", "function": {"name": "probe", "arguments": '{"port":8082}'}}
        self.assertEqual((True, None), campaign.validate_task_response(task, response(tool_calls=[call])))
        call["function"]["arguments"] = '{"port":8083}'
        self.assertEqual("tool_arguments_mismatch", campaign.validate_task_response(task, response(tool_calls=[call]))[1])

    def test_integrity_rejects_empty_special_tokens_and_loops(self) -> None:
        self.assertEqual("empty_output", campaign.completion_integrity(response(""))[1])
        self.assertEqual("invalid_special_token", campaign.completion_integrity(response("hello <|im_end|>"))[1])
        loop = " ".join(["alpha beta"] * 12)
        self.assertEqual("repetition_loop", campaign.completion_integrity(response(loop))[1])


class SummaryAndSelectionTests(unittest.TestCase):
    def _row(self, request_id: str, *, valid: bool = True, latency: float = 2.0, tokens: int = 20) -> dict:
        return {
            "request_id": request_id,
            "candidate": "qwen38-27b",
            "topology": "qwen27-replica-production",
            "mtp_enabled": False,
            "test_kind": "performance",
            "concurrency": 16,
            "requested_prompt_tokens": 512,
            "valid": valid,
            "success": valid,
            "latency_s": latency,
            "ttft_s": 0.4,
            "generated_tokens": tokens,
            "started_at": "2026-08-27T00:00:00Z",
            "completed_at": "2026-08-27T00:00:10Z",
            "failure_class": None if valid else "empty_output",
            "task_family": None,
        }

    def test_summary_counts_only_valid_output_as_goodput(self) -> None:
        summary = campaign.summarize_rows([self._row("a"), self._row("b", valid=False)])[0]
        self.assertEqual(2, summary["requests"])
        self.assertEqual(1, summary["valid_requests"])
        self.assertEqual(360.0, summary["jobs_per_hour"])
        self.assertEqual(2.0, summary["successful_output_tokens_per_s"])

    def test_summary_uses_latest_retry_for_a_request_id(self) -> None:
        failed = self._row("same", valid=False)
        recovered = self._row("same", valid=True)
        summary = campaign.summarize_rows([failed, recovered])[0]
        self.assertEqual(1, summary["requests"])
        self.assertEqual(1, summary["valid_requests"])

    def test_topology_selector_rejects_partial_cells(self) -> None:
        good = campaign.summarize_rows([self._row("a")])[0]
        faster_bad = {**good, "topology": "qwen27-dual-production", "jobs_per_hour": 9999, "valid_rate": 0.99}
        winner = campaign.choose_topology([faster_bad, good])
        self.assertEqual("qwen27-replica-production", winner["topology"])

    def test_context_proof_uses_measured_final_soak_slot_metadata(self) -> None:
        proven = {
            "candidate": "qwen38-27b",
            "run_id": "final-deep-context",
            "test_kind": "soak",
            "valid": True,
            "slot_depth": 65536,
            "requested_prompt_tokens": 60000,
            "prompt_tokens": 60200,
            "parallel_slots": 2,
            "request_payload_bytes": 230000,
        }
        nominal_only = {**proven, "run_id": "dual-context-d262144-c1", "slot_depth": 262144}
        invalid = {**proven, "valid": False, "slot_depth": 131072}
        self.assertEqual(65536, campaign.proven_spill_free_slot_tokens([nominal_only, invalid, proven]))
        self.assertEqual(
            {"slot_tokens": 65536, "parallel_slots": 2, "context_bytes": 230000},
            campaign.proven_routing_capacity([nominal_only, invalid, proven]),
        )

    def test_summary_keeps_dynamic_slot_depths_separate(self) -> None:
        first = {**self._row("a"), "slot_depth": 8192, "parallel_slots": 16}
        second = {**self._row("b"), "slot_depth": 16384, "parallel_slots": 16}
        self.assertEqual(2, len(campaign.summarize_rows([first, second])))

    def test_closed_loop_wall_time_ignores_resume_gap(self) -> None:
        rows = []
        for client in (0, 1):
            for request_id in range(2):
                rows.append(
                    {
                        **self._row(f"{client}-{request_id}", latency=2.0),
                        "run_id": "same-resumed-leg",
                        "client_id": client,
                        "started_at": "2026-08-27T00:00:00Z",
                        "completed_at": "2026-08-28T00:00:00Z",
                    }
                )
        summary = campaign.summarize_rows(rows)[0]
        self.assertEqual(4.0, summary["wall_s"])
        self.assertEqual(3600.0, summary["jobs_per_hour"])

    def test_energy_deltas_do_not_cross_resumed_watchdog_sessions(self) -> None:
        rows = [
            {"stage": "r", "telemetry_session": "a", "timestamp": "2026-08-27T00:00:00Z", "energy_j_counter": 10},
            {"stage": "r", "telemetry_session": "a", "timestamp": "2026-08-27T00:00:10Z", "energy_j_counter": 20},
            {"stage": "r", "telemetry_session": "b", "timestamp": "2026-08-28T00:00:00Z", "energy_j_counter": 1000},
            {"stage": "r", "telemetry_session": "b", "timestamp": "2026-08-28T00:00:10Z", "energy_j_counter": 1010},
        ]
        energy_j, duration_s = campaign._energy_totals(rows)
        self.assertEqual(20.0, energy_j)
        self.assertEqual(20.0, duration_s)

    def test_judge_packet_is_order_reversed(self) -> None:
        rows = [
            {"test_kind": "assay", "candidate": "base", "task_id": "t1", "seed": 1, "response_text": "base"},
            {"test_kind": "assay", "candidate": "new", "task_id": "t1", "seed": 1, "response_text": "new"},
        ]
        packet = campaign.build_judge_packet(rows, "base", "new")
        self.assertEqual(2, len(packet))
        self.assertEqual("base", packet[0]["blind_map"]["A"])
        self.assertEqual("new", packet[1]["blind_map"]["A"])
        reversed_models = campaign.build_judge_packet(rows, "new", "base")
        self.assertNotEqual(packet[0]["pair_id"], reversed_models[0]["pair_id"])

    def test_judge_packet_contains_task_and_serialized_tool_call(self) -> None:
        call = {"type": "function", "function": {"name": "probe", "arguments": "{}"}}
        rows = [
            {"test_kind": "assay", "candidate": "base", "task_id": "tool-02", "seed": 1, "response_text": "", "tool_calls": [call]},
            {"test_kind": "assay", "candidate": "new", "task_id": "tool-02", "seed": 1, "response_text": "new"},
        ]
        packet = campaign.build_judge_packet(rows, "base", "new", {"tool_execution"})
        self.assertTrue(packet[0]["prompt"])
        self.assertIn("tool_calls", packet[0]["A"])
        self.assertEqual([], campaign.build_judge_packet(rows, "base", "new", {"document_ocr"}))


class PromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scorecard = campaign.load_json(campaign.SOURCE_ROOT / "config" / "promotion-example.json")

    def test_example_clears_every_gate(self) -> None:
        verdict = campaign.evaluate_promotion(self.scorecard)
        self.assertTrue(verdict["all_gates_pass"], verdict)
        self.assertEqual("eligible_for_pin_only_canary", verdict["decision"])

    def test_one_family_regression_blocks_promotion(self) -> None:
        self.scorecard["candidate"]["family_pass_rates"]["tool_execution"] = 0.5
        verdict = campaign.evaluate_promotion(self.scorecard)
        self.assertFalse(verdict["gates"]["no_family_regression"])
        self.assertEqual("do_not_promote", verdict["decision"])

    def test_mtp_must_raise_valid_goodput(self) -> None:
        self.scorecard["candidate"]["mtp"] = {
            "enabled": True,
            "successful_output_goodput_delta": -0.01,
            "validity_regression": 0.0,
        }
        verdict = campaign.evaluate_promotion(self.scorecard)
        self.assertFalse(verdict["gates"]["mtp_net_goodput"])

    def test_unresolved_blind_disagreements_block_promotion(self) -> None:
        self.scorecard["candidate"]["blind_disagreements_requiring_adjudication"] = 1000
        verdict = campaign.evaluate_promotion(self.scorecard)
        self.assertFalse(verdict["gates"]["blind_judgment_coverage"])
        self.assertEqual("do_not_promote", verdict["decision"])

    def test_invalid_judgment_rows_reduce_coverage(self) -> None:
        rows = [
            {
                "contract_version": "qwen38-judgment.v1",
                "task_id": "summary-01",
                "seed": 38027,
                "order": 0,
                "valid": True,
                "mapped_winner": "qwen38-27b",
            },
            {
                "contract_version": "qwen38-judgment.v1",
                "task_id": "summary-01",
                "seed": 38027,
                "order": 1,
                "valid": False,
                "mapped_winner": None,
            },
        ]
        counts, disagreements = campaign._judgment_counts(rows, "qwen38-27b", "qwen3-30b-baseline")
        self.assertEqual({"wins": 0, "ties": 0, "losses": 0}, counts)
        self.assertEqual(1, disagreements)

    def test_missing_expected_judgment_pair_reduces_coverage(self) -> None:
        rows = [
            {
                "contract_version": "qwen38-judgment.v1",
                "task_id": "summary-01",
                "seed": 38027,
                "order": 0,
                "valid": True,
                "mapped_winner": "qwen38-27b",
            },
            {
                "contract_version": "qwen38-judgment.v1",
                "task_id": "summary-01",
                "seed": 38027,
                "order": 1,
                "valid": True,
                "mapped_winner": "qwen38-27b",
            },
        ]
        counts, disagreements = campaign._judgment_counts(
            rows,
            "qwen38-27b",
            "qwen3-30b-baseline",
            {("summary-01", 38027), ("summary-02", 38027)},
        )
        self.assertEqual(1, counts["wins"])
        self.assertEqual(1, disagreements)

    def test_scorecard_compiles_measured_routing_capacity(self) -> None:
        rows = [
            {
                "request_id": "base-assay",
                "candidate": "qwen3-30b-baseline",
                "test_kind": "assay",
                "task_id": "summary-01",
                "task_family": "summarization",
                "seed": 38027,
                "valid": True,
            },
            {
                "request_id": "candidate-assay",
                "candidate": "qwen38-27b",
                "test_kind": "assay",
                "task_id": "summary-01",
                "task_family": "summarization",
                "seed": 38027,
                "valid": True,
            },
            {
                "request_id": "deep",
                "run_id": "final-deep-context",
                "candidate": "qwen38-27b",
                "topology": "qwen27-replica-production",
                "mtp_enabled": False,
                "test_kind": "soak",
                "valid": True,
                "slot_depth": 65536,
                "parallel_slots": 2,
                "requested_prompt_tokens": 60000,
                "prompt_tokens": 60200,
                "request_payload_bytes": 231000,
            },
            {"request_id": "soak", "run_id": "final-soak", "candidate": "qwen38-27b", "topology": "qwen27-replica-production", "mtp_enabled": False, "valid": True},
        ]
        summaries = [
            {
                "candidate": "qwen3-30b-baseline",
                "topology": "baseline-production",
                "mtp_enabled": False,
                "test_kind": "performance",
                "concurrency": 16,
                "requested_prompt_tokens": 512,
                "jobs_per_hour": 100,
                "latency_p95_s": 10,
                "successful_output_tokens_per_s": 10,
            },
            {
                "candidate": "qwen38-27b",
                "topology": "qwen27-replica-production",
                "mtp_enabled": False,
                "test_kind": "performance",
                "concurrency": 16,
                "requested_prompt_tokens": 512,
                "jobs_per_hour": 130,
                "latency_p95_s": 11,
                "successful_output_tokens_per_s": 13,
            },
        ]
        judgments = [
            {"contract_version": "qwen38-judgment.v1", "valid": True, "task_id": "summary-01", "seed": 38027, "order": 0, "mapped_winner": "qwen38-27b"},
            {"contract_version": "qwen38-judgment.v1", "valid": True, "task_id": "summary-01", "seed": 38027, "order": 1, "mapped_winner": "qwen38-27b"},
        ]
        scorecard = campaign.compile_scorecard(
            rows,
            summaries,
            {"topology": "qwen27-replica-production", "mtp_enabled": False},
            judgments,
            [],
        )
        self.assertEqual(65536, scorecard["candidate"]["spill_free_slot_tokens"])
        self.assertEqual(2, scorecard["candidate"]["advertised_parallel_slots"])
        self.assertEqual(231000, scorecard["candidate"]["advertised_context_bytes"])


if __name__ == "__main__":
    unittest.main()

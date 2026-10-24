"""Unit tests for Turn-Boundary Mamba-2 State Checkpointing in OpenAIServerAdapter."""

from __future__ import annotations

import concurrent.futures
import io
import json
import threading
import time
import unittest
from unittest.mock import patch

from hearth.adapters.openai_server import (
    TURN_CHECKPOINT_ACK,
    OpenAIServerAdapter,
    build_turn_checkpointed_messages,
    checkpoint_messages_list,
    is_mamba_hybrid_target,
    reset_single_flight_prefix_cache,
    split_shared_context_pack,
)


class TestOpenAIServerTurnCheckpointing(unittest.TestCase):
    def test_is_mamba_hybrid_target_detection(self) -> None:
        self.assertTrue(is_mamba_hybrid_target(model_name="nemotron-3.5-bolt-74b-a9b"))
        self.assertTrue(is_mamba_hybrid_target(model_name="NVIDIA-Nemotron-3.5-Bolt-74B-A9B"))
        self.assertTrue(is_mamba_hybrid_target(backend_id="omen-nemotron-74b"))
        self.assertTrue(is_mamba_hybrid_target(backend_id="omen-vllm"))
        self.assertFalse(is_mamba_hybrid_target(model_name="qwen2.5-27b-instruct", backend_id="am4-vllm"))

    def test_split_repository_source_pack(self) -> None:
        prompt = (
            "=== BEGIN REPOSITORY SOURCE PACK ===\n"
            "FILE: hearth/localwork/service.py\n"
            "def dispatch(): pass\n"
            "=== END REPOSITORY SOURCE PACK ===\n\n"
            "Task: Audit dispatch invariants and edge cases."
        )
        split = split_shared_context_pack(prompt)
        self.assertIsNotNone(split)
        shared_pack, task_instruction = split  # type: ignore[misc]
        self.assertTrue(shared_pack.startswith("=== BEGIN REPOSITORY SOURCE PACK ==="))
        self.assertTrue(shared_pack.endswith("=== END REPOSITORY SOURCE PACK ==="))
        self.assertEqual(task_instruction, "Task: Audit dispatch invariants and edge cases.")

    def test_split_arch_pack_and_shared_context_end(self) -> None:
        arch_prompt = (
            "[ARCH_PACK]\n"
            "module Router: ports=[18090, 18091]\n"
            "[/ARCH_PACK]\n"
            "Verify router maxconn settings."
        )
        split_arch = split_shared_context_pack(arch_prompt)
        self.assertIsNotNone(split_arch)
        self.assertEqual(split_arch[0], "[ARCH_PACK]\nmodule Router: ports=[18090, 18091]\n[/ARCH_PACK]")
        self.assertEqual(split_arch[1], "Verify router maxconn settings.")

        html_marker_prompt = (
            "Shared AST Slice Header\n"
            "<!-- SHARED_CONTEXT_END -->\n"
            "Subtask 4: Check token ceiling."
        )
        split_marker = split_shared_context_pack(html_marker_prompt)
        self.assertIsNotNone(split_marker)
        self.assertEqual(split_marker[0], "Shared AST Slice Header\n<!-- SHARED_CONTEXT_END -->")
        self.assertEqual(split_marker[1], "Subtask 4: Check token ceiling.")

    def test_build_payload_splits_source_pack_for_nemotron(self) -> None:
        adapter = OpenAIServerAdapter(
            endpoint="http://127.0.0.1:18091",
            model="nemotron-3.5-bolt-74b-a9b",
            backend_id="omen-nemotron-74b",
            api_key="test-token",
        )
        prompt = (
            "=== BEGIN REPOSITORY SOURCE PACK ===\n"
            "def foo(x): return x + 1\n"
            "=== END REPOSITORY SOURCE PACK ===\n"
            "Explain foo(x)."
        )
        payload = adapter.build_payload(
            prompt,
            system="You are a code auditor.",
            max_tokens=160,
            temperature=0.0,
        )
        self.assertTrue(payload.get("cache_prompt"))
        self.assertEqual(
            payload["messages"],
            [
                {"role": "system", "content": "You are a code auditor."},
                {
                    "role": "user",
                    "content": "=== BEGIN REPOSITORY SOURCE PACK ===\ndef foo(x): return x + 1\n=== END REPOSITORY SOURCE PACK ===",
                },
                {"role": "assistant", "content": TURN_CHECKPOINT_ACK},
                {"role": "user", "content": "Explain foo(x)."},
            ],
        )

    def test_build_payload_splits_arch_pack_without_system_prompt(self) -> None:
        adapter = OpenAIServerAdapter(
            endpoint="http://127.0.0.1:18091",
            model="nemotron-3.5-bolt-74b-a9b",
            backend_id="omen-vllm",
            api_key="test-token",
        )
        prompt = "[ARCH_PACK]\nclass Engine: pass\n[/ARCH_PACK]\nList methods on Engine."
        payload = adapter.build_payload(prompt, max_tokens=128)
        self.assertTrue(payload.get("cache_prompt"))
        self.assertEqual(
            payload["messages"],
            [
                {"role": "user", "content": "[ARCH_PACK]\nclass Engine: pass\n[/ARCH_PACK]"},
                {"role": "assistant", "content": TURN_CHECKPOINT_ACK},
                {"role": "user", "content": "List methods on Engine."},
            ],
        )

    def test_single_block_prompt_left_untouched(self) -> None:
        adapter = OpenAIServerAdapter(
            endpoint="http://127.0.0.1:18091",
            model="nemotron-3.5-bolt-74b-a9b",
            backend_id="omen-nemotron-74b",
            api_key="test-token",
        )
        prompt = "Write a single-paragraph summary of PCIe peer-to-peer transfers."
        payload = adapter.build_payload(prompt, system="System instructions.", max_tokens=100)
        self.assertNotIn("cache_prompt", payload)
        self.assertEqual(
            payload["messages"],
            [
                {"role": "system", "content": "System instructions."},
                {"role": "user", "content": prompt},
            ],
        )

    def test_non_mamba_model_leaves_source_pack_single_turn(self) -> None:
        messages, cache_flag = build_turn_checkpointed_messages(
            "=== BEGIN REPOSITORY SOURCE PACK ===\ncode\n=== END REPOSITORY SOURCE PACK ===\nAudit.",
            system_prompt="System",
            model_name="qwen2.5-27b-instruct",
            backend_id="am4-vllm",
        )
        self.assertFalse(cache_flag)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")

    def test_generate_wire_and_response_parsing(self) -> None:
        adapter = OpenAIServerAdapter(
            endpoint="http://127.0.0.1:18091",
            model="nemotron-3.5-bolt-74b-a9b",
            backend_id="omen-nemotron-74b",
            api_key="test-token",
        )
        prompt = (
            "=== BEGIN REPOSITORY SOURCE PACK ===\n"
            "def bar(): return 42\n"
            "=== END REPOSITORY SOURCE PACK ===\n"
            "What does bar return?"
        )
        fake_response = {
            "id": "chatcmpl-test",
            "model": "nemotron-3.5-bolt-74b-a9b",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "bar returns 42."},
                }
            ],
            "usage": {
                "prompt_tokens": 2400,
                "completion_tokens": 12,
                "prompt_tokens_details": {"cached_tokens": 2200},
            },
            "timings": {
                "cache_n": 2200,
                "prompt_n": 200,
                "predicted_n": 12,
            },
        }

        class _FakeHTTPResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        with patch("urllib.request.urlopen") as mocked_urlopen:
            mocked_urlopen.return_value = _FakeHTTPResponse(
                json.dumps(fake_response).encode("utf-8")
            )
            res = adapter.generate(prompt, max_tokens=64, temperature=0.0)

        self.assertTrue(res["ok"])
        self.assertTrue(res["turn_checkpointed"])
        self.assertEqual(res["cached_tokens"], 2200)
        self.assertEqual(res["prompt_eval_tokens"], 200)
        self.assertTrue(res["wire_request"]["cache_prompt"])
        self.assertEqual(len(res["wire_request"]["messages"]), 3)
        self.assertEqual(res["wire_request"]["messages"][1]["content"], TURN_CHECKPOINT_ACK)

    def test_real_hearth_delivery_and_carry_prompts_share_identical_turn1_checkpoint(self) -> None:
        source_files_block = (
            "SOURCE FILES (exact 1-based line numbers; quote from the text AFTER the \"NNN| \" prefix):\n"
            "=== FILE hearth/delivery/sourcemap.py (sha256 a1b2c3d4e5f6, 120 lines) ===\n"
            "CODE:\n"
            "001| def render_for_model(sm): pass\n"
            "=== END hearth/delivery/sourcemap.py ==="
        )
        one_call_prompt = (
            "Produce a delivery JSON.\n\n"
            "REQUEST\nINTENT: Audit sourcemap.\n\n"
            f"{source_files_block}"
        )
        carry_work_msg = (
            "REQUEST\nINTENT: Audit sourcemap.\n\n"
            f"{source_files_block}\n\n"
            "STEP 1 OF 2 — WORK ONLY: Write # Notes and # Report."
        )
        carry_attach_msg = (
            "REQUEST\nINTENT: Audit sourcemap.\n\n"
            f"{source_files_block}\n\n"
            "STEP 2 OF 2 — ATTACH QUOTES:\n=== CARRIED DRAFT ===\n# Report\n\nParagraph 1."
        )

        msgs_one, cache_one = build_turn_checkpointed_messages(
            one_call_prompt, backend_id="omen-nemotron-74b"
        )
        self.assertTrue(cache_one)
        self.assertEqual(msgs_one[0]["content"], source_files_block)
        self.assertEqual(msgs_one[1]["content"], TURN_CHECKPOINT_ACK)

        msgs_work, cache_work = checkpoint_messages_list(
            [{"role": "system", "content": "Follow the user's instructions."},
             {"role": "user", "content": carry_work_msg}],
            backend_id="omen-nemotron-74b",
        )
        msgs_check, cache_check = checkpoint_messages_list(
            [{"role": "system", "content": "Follow the user's instructions."},
             {"role": "user", "content": carry_work_msg},
             {"role": "assistant", "content": "# Report\n\nParagraph 1."},
             {"role": "user", "content": "Check your report against the source."}],
            backend_id="omen-nemotron-74b",
        )
        msgs_attach, cache_attach = checkpoint_messages_list(
            [{"role": "system", "content": "Follow the user's instructions."},
             {"role": "user", "content": carry_attach_msg}],
            backend_id="omen-nemotron-74b",
        )
        self.assertTrue(cache_work and cache_check and cache_attach)
        # All three stages share the exact same [system, user(SOURCE FILES), assistant(ACK)] prefix!
        self.assertEqual(msgs_work[:3], msgs_check[:3])
        self.assertEqual(msgs_work[:3], msgs_attach[:3])
        self.assertEqual(msgs_work[1]["content"], source_files_block)
        # And check's first 5 messages match work's 4 messages + assistant draft!
        self.assertEqual(msgs_check[:4], msgs_work)

    def test_pack_files_jepa_pack_and_code_context_pack_split(self) -> None:
        # 1. <jepa_context_pack>
        jepa_prompt = "<jepa_context_pack>\n=== FILE a.py ===\n=== END a.py ===\n</jepa_context_pack>\nFix bug."
        s1 = split_shared_context_pack(jepa_prompt)
        self.assertIsNotNone(s1)
        self.assertTrue(s1[0].endswith("</jepa_context_pack>"))
        self.assertEqual(s1[1], "Fix bug.")

        # 2. <file path="..."> from inference._pack_files
        file_prompt = '<file path="a.py">\ndef f(): pass\n</file>\n\nExplain f().'
        s2 = split_shared_context_pack(file_prompt)
        self.assertIsNotNone(s2)
        self.assertEqual(s2[0], '<file path="a.py">\ndef f(): pass\n</file>')
        self.assertEqual(s2[1], "Explain f().")

        # 3. CONTEXT PACK:\n--- SOURCE ... and REPAIR:
        code_prompt = (
            "Return local-work-candidate.v1 JSON.\n\nTASK FAMILY: code_fix\nINTENT:\nFix a.py\n\n"
            "CONTEXT PACK:\n--- SOURCE a.py @ deadbeef ---\ndef f(): return 1\n\n"
            "REPAIR:\nPrevious output had invalid citation."
        )
        s3 = split_shared_context_pack(code_prompt)
        self.assertIsNotNone(s3)
        self.assertEqual(s3[0], "CONTEXT PACK:\n--- SOURCE a.py @ deadbeef ---\ndef f(): return 1")
        self.assertIn("REPAIR:\nPrevious output had invalid citation.", s3[1])

    def test_non_delivery_request_doc_prompts_share_identical_turn1_checkpoint(self) -> None:
        template = "You are a local work worker. Return local-work-candidate.v1 JSON."
        shared_files = [{"path": "hearth/adapters/openai_server.py", "lines": 120, "sha256": "abc123"}]
        shared_pack = "--- FILE hearth/adapters/openai_server.py ---\ndef split_shared_context_pack(prompt):\n    pass\n"
        req_doc_1 = {
            "intent": "Audit Turn-1 cache key boundaries.",
            "acceptance_criteria": ["Cite exact line ranges", "Identify prefix divergence"],
            "artifact_kind": "markdown",
            "target_path": None,
            "declared_paths": ["hearth/adapters/openai_server.py"],
            "source_files": shared_files,
            "source_pack": shared_pack,
        }
        req_doc_2 = {
            "intent": "Propose a unified diff for Mamba-2 checkpointing.",
            "acceptance_criteria": ["Must preserve backward compatibility"],
            "artifact_kind": "markdown",
            "target_path": "hearth/adapters/openai_server.py",
            "declared_paths": ["hearth/adapters/openai_server.py"],
            "source_files": shared_files,
            "source_pack": shared_pack,
        }
        prompt_1 = template + "\n\nREQUEST\n" + json.dumps(req_doc_1, sort_keys=True)
        prompt_2 = (
            template
            + "\n\nREQUEST\n"
            + json.dumps(req_doc_2, sort_keys=True)
            + "\n\nREPAIR: The response below was rejected as malformed.\nREJECTED RESPONSE:\n{}"
        )
        msgs_1, cache_1 = build_turn_checkpointed_messages(prompt_1, backend_id="omen-nemotron-74b")
        msgs_2, cache_2 = build_turn_checkpointed_messages(prompt_2, backend_id="omen-nemotron-74b")
        self.assertTrue(cache_1 and cache_2)
        self.assertEqual(len(msgs_1), 3)
        self.assertEqual(len(msgs_2), 3)
        # Turn 1 (shared context pack) + Assistant ACK are 100% byte-identical across both tasks!
        self.assertEqual(msgs_1[:2], msgs_2[:2])
        self.assertIn("SOURCE PACK\n--- FILE hearth/adapters/openai_server.py ---", msgs_1[0]["content"])
        self.assertIn("Audit Turn-1 cache key boundaries.", msgs_1[2]["content"])
        self.assertIn("REPAIR: The response below was rejected as malformed.", msgs_2[2]["content"])

    def test_non_mamba_am4_vllm_request_doc_reorders_shared_context_prefix(self) -> None:
        template = "You are a local work worker. Return local-work-candidate.v1 JSON."
        shared_files = [{"path": "hearth/adapters/openai_server.py", "lines": 700, "sha256": "abc123"}]
        shared_pack = "--- SOURCE hearth/adapters/openai_server.py @ deadbeef ---\n" + ("def f(x): return x + 1\n" * 400)
        req_doc_1 = {
            "intent": "Task A: Audit Turn-1 cache key boundaries.",
            "acceptance_criteria": ["Criterion A1", "Criterion A2"],
            "artifact_kind": "markdown",
            "target_path": None,
            "declared_paths": ["hearth/adapters/openai_server.py"],
            "source_files": shared_files,
            "source_pack": shared_pack,
        }
        req_doc_2 = {
            "intent": "Task B: Propose a unified diff for APC prefix ordering.",
            "acceptance_criteria": ["Criterion B1"],
            "artifact_kind": "unified_diff",
            "target_path": "hearth/adapters/openai_server.py",
            "declared_paths": ["hearth/adapters/openai_server.py"],
            "source_files": shared_files,
            "source_pack": shared_pack,
        }
        prompt_1 = template + "\n\nREQUEST\n" + json.dumps(req_doc_1, sort_keys=True)
        prompt_2 = template + "\n\nREQUEST\n" + json.dumps(req_doc_2, sort_keys=True)

        msgs_1, cache_1 = build_turn_checkpointed_messages(
            prompt_1, model_name="am4-dense-27b", backend_id="am4-vllm"
        )
        msgs_2, cache_2 = build_turn_checkpointed_messages(
            prompt_2, model_name="am4-dense-27b", backend_id="am4-vllm"
        )
        self.assertFalse(cache_1)
        self.assertFalse(cache_2)
        self.assertEqual(len(msgs_1), 1)
        self.assertEqual(len(msgs_2), 1)

        c1 = msgs_1[0]["content"]
        c2 = msgs_2[0]["content"]
        expected_shared_prefix = split_shared_context_pack(prompt_1)[0]  # type: ignore[index]
        self.assertTrue(c1.startswith(expected_shared_prefix + "\n\nREQUEST\n{"))
        self.assertTrue(c2.startswith(expected_shared_prefix + "\n\nREQUEST\n{"))
        # Both prompts share the exact same multi-kilobyte prefix from byte 0 up through the end of shared_pack!
        self.assertEqual(c1[: len(expected_shared_prefix)], c2[: len(expected_shared_prefix)])
        self.assertIn("Task A: Audit Turn-1 cache key boundaries.", c1[len(expected_shared_prefix) :])
        self.assertIn("Task B: Propose a unified diff for APC prefix ordering.", c2[len(expected_shared_prefix) :])

    def test_single_flight_cold_prefix_coalescing_waits_for_leader_then_runs_followers_in_parallel(self) -> None:
        reset_single_flight_prefix_cache()
        adapter = OpenAIServerAdapter(
            endpoint="http://10.44.0.2:8090",
            model="am4-dense-27b",
            backend_id="am4-vllm",
            api_key="test-token",
        )
        shared_pack = "=== BEGIN REPOSITORY SOURCE PACK ===\n" + ("x = 1\n" * 200) + "=== END REPOSITORY SOURCE PACK ==="
        prompts = [f"{shared_pack}\nTask #{i}: summarize." for i in range(4)]

        call_lock = threading.Lock()
        active_count = 0
        max_active_during_leader = 0
        max_active_during_followers = 0
        call_order: list[int] = []
        leader_entered = threading.Event()
        release_leader = threading.Event()
        followers_all_active = threading.Event()

        class _FakeHTTPResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        def fake_urlopen(req, timeout=None):
            nonlocal active_count, max_active_during_leader, max_active_during_followers
            with call_lock:
                call_idx = len(call_order)
                call_order.append(call_idx)
                active_count += 1
                if call_idx == 0:
                    max_active_during_leader = max(max_active_during_leader, active_count)
                    leader_entered.set()
                else:
                    max_active_during_followers = max(max_active_during_followers, active_count)
                    if active_count >= 3:
                        followers_all_active.set()

            try:
                if call_idx == 0:
                    # Wait briefly to give followers time to arrive and block on the leader's Event
                    release_leader.wait(timeout=2.0)
                    with call_lock:
                        max_active_during_leader = max(max_active_during_leader, active_count)
                else:
                    # Followers should all enter urlopen in parallel once the leader finishes
                    followers_all_active.wait(timeout=2.0)
                body = {
                    "id": f"chatcmpl-{call_idx}",
                    "model": "am4-dense-27b",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": f"done-{call_idx}"},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1000,
                        "completion_tokens": 8,
                        "prompt_tokens_details": {"cached_tokens": 0 if call_idx == 0 else 980},
                    },
                }
                return _FakeHTTPResponse(json.dumps(body).encode("utf-8"))
            finally:
                with call_lock:
                    active_count -= 1

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                f0 = pool.submit(adapter.generate, prompts[0], max_tokens=16, temperature=0.0)
                self.assertTrue(leader_entered.wait(timeout=2.0))
                follower_futures = [
                    pool.submit(adapter.generate, prompts[i], max_tokens=16, temperature=0.0)
                    for i in range(1, 4)
                ]
                # Give followers 50ms to enter generate() and block on single-flight Event
                time.sleep(0.05)
                release_leader.set()
                results = [f0.result(timeout=5.0)] + [f.result(timeout=5.0) for f in follower_futures]

        self.assertTrue(all(r["ok"] for r in results))
        # Leader ran completely alone (max_active_during_leader == 1) while followers waited!
        self.assertEqual(max_active_during_leader, 1)
        # Once leader completed, all 3 followers ran in parallel (max_active_during_followers == 3)!
        self.assertEqual(max_active_during_followers, 3)
        self.assertEqual(results[0].get("single_flight_role"), "leader")
        for r in results[1:]:
            self.assertEqual(r.get("single_flight_role"), "follower")
            self.assertGreater(r.get("single_flight_wait_ms", 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
